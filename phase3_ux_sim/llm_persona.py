"""Sinh danh sách ~100 User Personas & quyết định hành động đọc-hỏi nối tiếp bằng LLM."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import logging
import random
import re
import time
from typing import Any, Deque, Dict, List, Optional, Tuple
import httpx

from .config import SimConfig

logger = logging.getLogger(__name__)


@dataclass
class _ModelBucket:
    """Trạng thái giới hạn RPM/RPD độc lập cho từng model trong pool."""

    name: str
    timestamps: Deque[float] = field(default_factory=deque)
    daily_used: int = 0
    cooldown_until: float = 0.0
    disabled: bool = False


class MultiModelRateLimiterPool:
    """
    Điều phối xoay vòng & failover giữa nhiều model Gemini Flash-Lite (per-model RPD/RPM).
    Giữ giới hạn an toàn dưới 30 RPM / 500 RPD cho từng model và tự động chuyển model khi gặp 429/503.
    """

    def __init__(self, models: List[str], rpm_limit: int = 25, rpd_limit: int = 480) -> None:
        unique_models = list(dict.fromkeys(m.strip() for m in models if m and m.strip()))
        self.models: List[str] = unique_models or ["gemini-3.1-flash-lite"]
        self.rpm_limit = max(1, rpm_limit)
        self.rpd_limit = max(1, rpd_limit)
        self._buckets: Dict[str, _ModelBucket] = {m: _ModelBucket(name=m) for m in self.models}
        self._rr_idx: int = 0
        self._lock = asyncio.Lock()

        self.calls_by_model: Dict[str, int] = {m: 0 for m in self.models}
        self.failures_by_model: Dict[str, int] = {m: 0 for m in self.models}
        self.model_failovers: int = 0
        self.throttle_waits: int = 0
        self.total_wait_ms: float = 0.0

    def _prune_old(self, bucket: _ModelBucket, now: float) -> None:
        cutoff = now - 60.0
        while bucket.timestamps and bucket.timestamps[0] <= cutoff:
            bucket.timestamps.popleft()

    async def acquire(self, excluded: Optional[set[str]] = None) -> Optional[str]:
        """Chọn model sẵn sàng nhất theo vòng tròn, tự chờ nếu chạm ngưỡng RPM."""
        excluded = excluded or set()
        while True:
            wait_sec = 0.0
            async with self._lock:
                now = time.monotonic()
                candidates: List[Tuple[int, float, int, _ModelBucket]] = []
                n = len(self.models)
                for offset in range(n):
                    idx = (self._rr_idx + offset) % n
                    m_name = self.models[idx]
                    bucket = self._buckets[m_name]
                    if m_name in excluded or bucket.disabled or bucket.daily_used >= self.rpd_limit:
                        continue
                    self._prune_old(bucket, now)
                    rpm_wait = (
                        max(0.0, (bucket.timestamps[0] + 60.05) - now)
                        if len(bucket.timestamps) >= self.rpm_limit
                        else 0.0
                    )
                    cd_wait = max(0.0, bucket.cooldown_until - now)
                    needed_wait = max(rpm_wait, cd_wait)
                    candidates.append((0 if needed_wait == 0.0 else 1, needed_wait, offset, bucket))

                if not candidates:
                    return None

                candidates.sort(key=lambda item: (item[0], item[1], item[2]))
                _, wait_sec, _, chosen = candidates[0]
                if wait_sec <= 0.0:
                    chosen.timestamps.append(now)
                    chosen.daily_used += 1
                    self._rr_idx = (self.models.index(chosen.name) + 1) % len(self.models)
                    return chosen.name

                self.throttle_waits += 1
                self.total_wait_ms += wait_sec * 1000.0

            await asyncio.sleep(min(wait_sec, 5.0))

    async def report_result(
        self,
        model_name: str,
        *,
        status_code: Optional[int],
        success: bool,
    ) -> None:
        """Ghi nhận kết quả gọi API để kích hoạt cooldown hoặc tắt model không tồn tại."""
        async with self._lock:
            bucket = self._buckets.get(model_name)
            if not bucket:
                return
            if success:
                self.calls_by_model[model_name] = self.calls_by_model.get(model_name, 0) + 1
                return

            self.failures_by_model[model_name] = self.failures_by_model.get(model_name, 0) + 1
            now = time.monotonic()
            if status_code == 404:
                bucket.disabled = True
            elif status_code in (429, 503):
                bucket.cooldown_until = max(bucket.cooldown_until, now + 60.0)
                if len(self.models) > 1:
                    self.model_failovers += 1

    def get_stats(self) -> Dict[str, Any]:
        """Xuất thống kê sử dụng và hạn mức còn lại của toàn bộ model pool."""
        return {
            "models": list(self.models),
            "rpm_limit_per_model": self.rpm_limit,
            "rpd_limit_per_model": self.rpd_limit,
            "combined_rpd_capacity": len(self.models) * self.rpd_limit,
            "calls_by_model": dict(self.calls_by_model),
            "failures_by_model": dict(self.failures_by_model),
            "model_failovers": self.model_failovers,
            "throttle_waits": self.throttle_waits,
            "total_wait_ms": round(self.total_wait_ms, 1),
            "rpd_remaining_by_model": {
                m: max(0, self.rpd_limit - b.daily_used) for m, b in self._buckets.items()
            },
        }


_POOLS_BY_KEY: Dict[Tuple[int, int], MultiModelRateLimiterPool] = {}


def get_llm_pool(cfg: SimConfig) -> MultiModelRateLimiterPool:
    """Lấy hoặc tạo MultiModelRateLimiterPool gắn với config và event loop hiện tại."""
    loop = asyncio.get_running_loop()
    key = (id(cfg), id(loop))
    pool = _POOLS_BY_KEY.get(key)
    if pool is None or pool.models != list(dict.fromkeys(cfg.llm_models)):
        pool = MultiModelRateLimiterPool(
            models=cfg.llm_models,
            rpm_limit=cfg.llm_rpm_limit,
            rpd_limit=cfg.llm_rpd_limit,
        )
        _POOLS_BY_KEY[key] = pool
    return pool


_FALLBACK_TOPICS = [
    ("Phòng Nhân sự", "Tìm hiểu quy trình xin nghỉ phép và ngày phép năm"),
    ("Phòng Tài chính", "Tra cứu hạn mức thanh toán công tác phí và hóa đơn"),
    ("Phòng Kỹ thuật", "Kiểm tra quy chế làm việc từ xa và bảo mật 2FA"),
    ("Phòng Kinh doanh", "Hỏi về quy trình phê duyệt hợp đồng trên 500 triệu"),
    ("Phòng Pháp chế", "Rà soát chính sách bảo vệ dữ liệu khách hàng và phân quyền"),
]


def _extract_json_block(raw_text: str) -> Any:
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


async def _call_google_llm(
    cfg: SimConfig,
    prompt: str,
    temperature: float = 0.7,
) -> Optional[str]:
    if cfg.offline_llm or not cfg.google_api_key:
        return None
    pool = get_llm_pool(cfg)
    base = cfg.llm_api_url.rstrip("/")
    headers = {"Content-Type": "application/json", "x-goog-api-key": cfg.google_api_key}
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }

    tried_models: set[str] = set()
    # trust_env=False: tránh lỗi httpx khi NO_PROXY chứa '[::1]' trên Windows.
    async with httpx.AsyncClient(timeout=35.0, trust_env=False) as client:
        for _ in range(len(pool.models)):
            chosen_model = await pool.acquire(excluded=tried_models)
            if not chosen_model:
                break
            tried_models.add(chosen_model)
            endpoint = f"{base}/v1beta/models/{chosen_model}:generateContent"
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code in (404, 429, 503):
                    await pool.report_result(chosen_model, status_code=resp.status_code, success=False)
                    logger.warning(
                        "Model %s trả HTTP %s — chuyển sang model dự phòng khác trong pool.",
                        chosen_model,
                        resp.status_code,
                    )
                    continue
                resp.raise_for_status()
                parts = resp.json()["candidates"][0]["content"]["parts"]
                final = [p.get("text", "") for p in parts if p.get("text") and not p.get("thought")]
                text = "".join(final).strip() if final else "".join(p.get("text", "") for p in parts).strip()
                await pool.report_result(chosen_model, status_code=resp.status_code, success=True)
                return text or None
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                await pool.report_result(chosen_model, status_code=status, success=False)
                logger.warning("LLM persona generator fallback (%s): %s", chosen_model, exc)
    return None


async def generate_user_profiles_via_llm(
    cfg: SimConfig,
    count_needed: int,
    start_index: int = 1,
    run_suffix: str = "sim",
) -> List[Dict[str, str]]:
    """
    Gọi LLM sinh danh sách tài khoản (username, email, password, full_name, department, persona_goal).
    Chia batch 20 user/call để gọn token; fallback an toàn nếu offline hoặc provider quá tải.
    """
    profiles: List[Dict[str, str]] = []
    batch_size = 20

    while len(profiles) < count_needed:
        need = min(batch_size, count_needed - len(profiles))
        idx_from = start_index + len(profiles)
        prompt = (
            f"Hãy tạo đúng {need} tài khoản nhân viên doanh nghiệp Việt Nam khác nhau dạng JSON array.\n"
            f"Mỗi phần tử gồm đúng các key: username, full_name, department_name, persona_goal, password.\n"
            f"Yêu cầu: username viết liền không dấu (vd: minh_tran_{idx_from}), password tối thiểu 12 ký tự có chữ hoa, thường, số và ký tự đặc biệt (vd: Enterprise@{idx_from}26).\n"
            f"Chỉ trả về JSON array thuần, không thêm giải thích."
        )
        raw = await _call_google_llm(cfg, prompt, temperature=0.8)
        parsed_batch: List[Dict[str, str]] = []
        if raw:
            try:
                data = _extract_json_block(raw)
                if isinstance(data, list):
                    for offset, row in enumerate(data[:need]):
                        if not isinstance(row, dict):
                            continue
                        seq = idx_from + offset
                        uname = re.sub(r"[^a-zA-Z0-9_]", "", str(row.get("username") or f"user_{seq}")).lower()[:32]
                        uname = f"{uname}_{seq}"
                        pwd = str(row.get("password") or f"SimUser@{seq}26!")
                        if len(pwd) < 8:
                            pwd = f"SimUser@{seq}26!"
                        parsed_batch.append({
                            "username": uname,
                            "email": f"{uname}@sim.{run_suffix}.vn",
                            "password": pwd,
                            "full_name": str(row.get("full_name") or f"Nhân viên {seq}")[:120],
                            "department_name": str(row.get("department_name") or _FALLBACK_TOPICS[seq % len(_FALLBACK_TOPICS)][0])[:80],
                            "persona_goal": str(row.get("persona_goal") or _FALLBACK_TOPICS[seq % len(_FALLBACK_TOPICS)][1])[:300],
                        })
            except (ValueError, TypeError) as exc:
                logger.warning("Không parse được JSON persona batch: %s", exc)

        # Bù đủ số lượng bằng mẫu xác định nếu LLM không trả đủ hoặc đang offline
        while len(parsed_batch) < need:
            seq = idx_from + len(parsed_batch)
            dept, goal = _FALLBACK_TOPICS[seq % len(_FALLBACK_TOPICS)]
            uname = f"nv_sim_{seq}"
            parsed_batch.append({
                "username": uname,
                "email": f"{uname}@sim.{run_suffix}.vn",
                "password": f"SimPass@{seq}2026!",
                "full_name": f"Nhân viên Mô phỏng {seq}",
                "department_name": dept,
                "persona_goal": goal,
            })

        profiles.extend(parsed_batch[:need])

    return profiles[:count_needed]


_BEHAVIOR_PROFILES = [
    "Lướt xem danh sách tài liệu và tìm kiếm nhanh rồi thoát, không cần hỏi chatbot nếu đã thấy ý chính.",
    "Tìm kiếm hybrid vài từ khóa rồi chỉ hỏi 1 câu chat ngắn khi thực sự cần làm rõ điều kiện.",
    "Nhân viên bận rộn: thao tác ngẫu nhiên 1-2 việc rồi rời phiên sớm.",
    "Người dùng kỹ tính: kiểm tra danh mục tài liệu, hỏi đáp RAG và gửi đánh giá hài lòng/không hài lòng.",
]


def pick_random_behavior_profile() -> str:
    """Chọn ngẫu nhiên phong cách phiên làm việc cho một user ảo."""
    return random.choice(_BEHAVIOR_PROFILES)


def _pick_random_fallback_action(
    *,
    persona_goal: str,
    step_index: int,
    last_llm_reply: Optional[str],
    last_http_status: Optional[int],
    remaining_llm_queries: int,
) -> Dict[str, Any]:
    """Sinh hành động ngẫu nhiên có trọng số thực tế, tôn trọng trần LLM query."""
    query_variants = [
        persona_goal,
        f"Tóm tắt ngắn gọn: {persona_goal}",
        f"Điều kiện và ngoại lệ liên quan đến {persona_goal.lower()}",
        f"Quy trình áp dụng thực tế cho {persona_goal.lower()}",
    ]
    chosen_query = random.choice(query_variants)

    if last_http_status == 429:
        act = random.choices(["list_documents", "search_hybrid", "exit_session"], weights=[0.45, 0.25, 0.30])[0]
        return {
            "action": act,
            "query": chosen_query if act == "search_hybrid" else None,
            "feedback_rating": None,
            "reasoning": "Gặp giới hạn tốc độ 429 nên ngẫu nhiên chuyển sang thao tác nhẹ hoặc thoát.",
        }

    if last_llm_reply and "không tìm thấy" in last_llm_reply.lower() and random.random() < 0.7:
        return {
            "action": "submit_feedback",
            "query": f"Chi tiết thêm về: {persona_goal}",
            "feedback_rating": "THUMBS_DOWN",
            "reasoning": "Câu trả lời trước chưa có thông tin nên gửi THUMBS_DOWN.",
        }

    candidates: List[str] = ["search_hybrid", "list_documents"]
    weights: List[float] = [0.42, 0.33]

    if remaining_llm_queries > 0:
        candidates.extend(["chat_stream", "chat_query"])
        weights.extend([0.24, 0.11])

    if last_llm_reply:
        candidates.append("submit_feedback")
        weights.append(0.22)

    if step_index > 0:
        candidates.append("exit_session")
        weights.append(0.25)

    picked = random.choices(candidates, weights=weights, k=1)[0]
    if picked == "submit_feedback":
        rating = random.choices(["THUMBS_UP", "THUMBS_DOWN"], weights=[0.85, 0.15])[0]
        return {
            "action": "submit_feedback",
            "query": None,
            "feedback_rating": rating,
            "reasoning": f"Ngẫu nhiên đánh giá câu trả lời vừa đọc ({rating}).",
        }
    if picked in ("list_documents", "exit_session"):
        return {
            "action": picked,
            "query": None,
            "feedback_rating": None,
            "reasoning": "Người dùng chọn xem danh mục hoặc kết thúc phiên tự nhiên.",
        }
    return {
        "action": picked,
        "query": chosen_query,
        "feedback_rating": None,
        "reasoning": f"Người dùng chọn ngẫu nhiên thao tác {picked} theo nhu cầu công việc.",
    }


async def decide_next_action_via_llm(
    cfg: SimConfig,
    *,
    persona_goal: str,
    department_name: str,
    step_index: int,
    total_steps: int,
    last_query: Optional[str],
    last_llm_reply: Optional[str],
    last_http_status: Optional[int],
    behavior_profile: Optional[str] = None,
    remaining_llm_queries: int = 20,
) -> Dict[str, Any]:
    """
    Gửi kèm `last_query` + `last_llm_reply` (câu trả lời RAG gần nhất user vừa đọc) cho LLM
    để mô phỏng hành động tiếp theo ngẫu nhiên như người đọc thật, không gò ép làm đủ mọi bước.
    """
    default_action = _pick_random_fallback_action(
        persona_goal=persona_goal,
        step_index=step_index,
        last_llm_reply=last_llm_reply,
        last_http_status=last_http_status,
        remaining_llm_queries=remaining_llm_queries,
    )

    allowed_list = (
        "['chat_stream', 'chat_query', 'search_hybrid', 'list_documents', 'submit_feedback', 'exit_session']"
        if remaining_llm_queries > 0
        else "['search_hybrid', 'list_documents', 'submit_feedback', 'exit_session']"
    )
    behavior = behavior_profile or pick_random_behavior_profile()

    prompt = (
        "Bạn đang đóng vai nhân viên thật dùng Chatbot Tri thức Doanh nghiệp.\n"
        f"- Phòng ban: {department_name}\n"
        f"- Mục tiêu cá nhân: {persona_goal}\n"
        f"- Phong cách phiên hiện tại: {behavior}\n"
        f"- Bước hiện tại: {step_index + 1}/{total_steps} (không bắt buộc phải làm hết mọi bước, có thể exit_session sớm)\n"
        f"- Lượt hỏi RAG AI (chat_stream/chat_query) toàn run còn lại: {remaining_llm_queries}\n"
        f"- Truy vấn gần nhất bạn đã hỏi: {last_query or '(chưa có)'}\n"
        f"- Câu trả lời gần nhất bạn vừa đọc từ AI:\n{(last_llm_reply or '(chưa có)')[:800]}\n"
        f"- Trạng thái HTTP bước trước: {last_http_status or 200}\n\n"
        "Lưu ý: Người dùng thật không phải lúc nào cũng hỏi AI; họ có thể chỉ xem danh sách tài liệu, "
        "tìm kiếm hybrid hoặc thoát sớm.\n"
        f"Chọn ngẫu nhiên MỘT hành động hợp lý nhất trong: {allowed_list}.\n"
        "Trả về DUY NHẤT 1 JSON object gồm keys: action, query (string hoặc null), "
        "feedback_rating ('THUMBS_UP' | 'THUMBS_DOWN' | null), reasoning (ngắn gọn tiếng Việt)."
    )

    raw = await _call_google_llm(cfg, prompt, temperature=0.8)
    if not raw:
        return default_action

    allowed_set = {
        "search_hybrid",
        "list_documents",
        "submit_feedback",
        "exit_session",
    }
    if remaining_llm_queries > 0:
        allowed_set.update({"chat_stream", "chat_query"})

    try:
        parsed = _extract_json_block(raw)
        if isinstance(parsed, dict) and parsed.get("action") in allowed_set:
            return {
                "action": parsed["action"],
                "query": parsed.get("query") or default_action["query"],
                "feedback_rating": parsed.get("feedback_rating"),
                "reasoning": str(parsed.get("reasoning") or "LLM quyết định ngẫu nhiên theo ngữ cảnh đọc thật.")[:250],
            }
    except (ValueError, TypeError):
        pass

    return default_action
