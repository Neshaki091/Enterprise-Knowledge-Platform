"""Mô phỏng phiên làm việc thật của từng User và lưu toàn bộ vết UX lên Supabase Cloud DB."""

import asyncio
import json
import random
import time
from typing import Dict, Any, List, Optional, Tuple
import httpx

from .config import SimConfig
from .llm_persona import decide_next_action_via_llm, pick_random_behavior_profile
from .storage import SimStorage, UxSimEvent, UxSimUser


class LlmQueryBudget:
    """Ngân sách tra cứu RAG LLM (chat_stream/chat_query) toàn phiên chạy để giữ egress < 0,8 GB/tháng."""

    def __init__(self, max_queries: int = 20) -> None:
        self.max_queries = max(0, max_queries)
        self._used = 0
        self._downgraded = 0
        self._lock = asyncio.Lock()

    async def try_consume(self) -> bool:
        """Giành 1 lượt hỏi RAG Backend; trả về False nếu đã chạm ngưỡng tối đa."""
        async with self._lock:
            if self._used >= self.max_queries:
                self._downgraded += 1
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_queries - self._used)

    @property
    def downgraded(self) -> int:
        return self._downgraded

    def get_stats(self) -> Dict[str, int]:
        return {
            "max_llm_queries": self.max_queries,
            "used_llm_queries": self._used,
            "remaining_llm_queries": self.remaining,
            "downgraded_to_lightweight": self._downgraded,
        }


async def _run_sse_chat(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    query: str,
) -> Tuple[int, float, float, str, Dict[str, Any], int, int, int, Optional[str]]:
    """Gọi POST /api/v1/chat/stream và đo TTFB + toàn bộ câu trả lời/context + egress bytes."""
    t0 = time.perf_counter()
    ttfb_ms = 0.0
    tokens_out: List[str] = []
    chunks_ctx: List[Dict[str, Any]] = []
    comp_stats: Dict[str, Any] = {}
    egress_bytes = 300  # Ước lượng tối thiểu HTTP response headers

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"query": query, "top_k": 3, "mode": "hybrid"},
            headers=headers,
        ) as resp:
            status_code = resp.status_code
            if status_code != 200:
                raw_err = await resp.aread()
                egress_bytes += len(raw_err)
                err_body = raw_err.decode("utf-8", errors="replace")[:300]
                return status_code, 0.0, (time.perf_counter() - t0) * 1000.0, "", {"egress_bytes": egress_bytes}, 0, 0, 0, err_body

            async for raw_line in resp.aiter_lines():
                egress_bytes += len(raw_line.encode("utf-8")) + 1
                line = raw_line.strip()
                if not line.startswith("data: "):
                    continue
                if ttfb_ms == 0.0:
                    ttfb_ms = (time.perf_counter() - t0) * 1000.0
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "chunks":
                    chunks_ctx = ev.get("data", [])
                    comp_stats = ev.get("compression_stats", {}) or {}
                elif etype == "token":
                    tokens_out.append(str(ev.get("content", "")))
                elif etype == "error":
                    return 500, ttfb_ms, (time.perf_counter() - t0) * 1000.0, "", {"chunks": chunks_ctx, "egress_bytes": egress_bytes}, 0, 0, 0, str(ev.get("message"))
    except httpx.HTTPError as exc:
        return 599, 0.0, (time.perf_counter() - t0) * 1000.0, "", {"egress_bytes": egress_bytes}, 0, 0, 0, str(exc)

    total_ms = (time.perf_counter() - t0) * 1000.0
    reply = "".join(tokens_out).strip()
    in_tok = int(comp_stats.get("original_tokens", 0))
    comp_tok = int(comp_stats.get("compressed_tokens", 0))
    out_tok = max(0, len(reply) // 4)
    ctx_payload = {
        "egress_bytes": egress_bytes,
        "compression_stats": comp_stats,
        "retrieved_chunks_preview": [
            {
                "document_title": c.get("document_title"),
                "heading": c.get("heading"),
                "rrf_score": c.get("rrf_score"),
            }
            for c in chunks_ctx
        ],
    }
    return 200, ttfb_ms, total_ms, reply, ctx_payload, in_tok, comp_tok, out_tok, None


async def simulate_single_user_session(
    cfg: SimConfig,
    storage: SimStorage,
    client: httpx.AsyncClient,
    run_id: str,
    user: UxSimUser,
    steps_per_user: int,
    jitter_max_sec: float = 0.25,
    query_budget: Optional[LlmQueryBudget] = None,
) -> List[UxSimEvent]:
    """Chạy 1 phiên người dùng ngẫu nhiên: Login -> random 1..N bước với trần LLM query."""
    events: List[UxSimEvent] = []

    # 1. Login bằng tài khoản đã lưu
    t_login = time.perf_counter()
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": user.password},
    )
    login_ms = (time.perf_counter() - t_login) * 1000.0
    login_ev = UxSimEvent(
        run_id=run_id,
        user_id=user.backend_user_id,
        tenant_id=user.tenant_id,
        step_index=0,
        action_type="login",
        context={"egress_bytes": len(login_res.content) + 300},
        http_status=login_res.status_code,
        ttfb_ms=login_ms,
        total_latency_ms=login_ms,
        error_message=None if login_res.status_code == 200 else login_res.text[:250],
    )
    await storage.record_event(login_ev)
    events.append(login_ev)

    if login_res.status_code != 200:
        return events

    access_token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    last_query: Optional[str] = None
    last_llm_reply: Optional[str] = None
    last_http_status: int = 200
    behavior_profile = pick_random_behavior_profile()
    actual_steps = random.randint(1, max(1, steps_per_user))

    # 2. Vòng lặp hành động ngẫu nhiên (không ép buộc làm đủ toàn bộ hành động)
    for step_idx in range(actual_steps):
        if jitter_max_sec > 0:
            await asyncio.sleep(random.uniform(0.02, jitter_max_sec))

        decision = await decide_next_action_via_llm(
            cfg=cfg,
            persona_goal=user.persona_goal,
            department_name=user.department_name,
            step_index=step_idx,
            total_steps=actual_steps,
            last_query=last_query,
            last_llm_reply=last_llm_reply,
            last_http_status=last_http_status,
            behavior_profile=behavior_profile,
            remaining_llm_queries=query_budget.remaining if query_budget else cfg.max_llm_queries_per_run,
        )
        action = decision["action"]
        q_text = decision.get("query") or user.persona_goal
        reasoning = decision.get("reasoning") or ""

        if action in ("chat_stream", "chat_query") and query_budget is not None:
            if not await query_budget.try_consume():
                action = random.choices(["search_hybrid", "list_documents"], weights=[0.65, 0.35])[0]
                reasoning = (
                    f"{reasoning} [Đã chạm trần {query_budget.max_queries} LLM query/run — chuyển sang {action}]"
                )[:250]

        if action == "exit_session":
            ev = UxSimEvent(
                run_id=run_id,
                user_id=user.backend_user_id,
                tenant_id=user.tenant_id,
                step_index=step_idx + 1,
                action_type="exit_session",
                context={"egress_bytes": 0, "behavior_profile": behavior_profile},
                http_status=200,
                llm_reasoning=reasoning,
            )
            await storage.record_event(ev)
            events.append(ev)
            break

        if action == "chat_stream":
            code, ttfb, total_ms, reply, ctx_json, in_t, comp_t, out_t, err = await _run_sse_chat(
                client, headers, q_text
            )
            last_query = q_text
            if reply:
                last_llm_reply = reply
            last_http_status = code
            ev = UxSimEvent(
                run_id=run_id,
                user_id=user.backend_user_id,
                tenant_id=user.tenant_id,
                step_index=step_idx + 1,
                action_type="chat_stream",
                query=q_text,
                llm_reply=reply,
                context=ctx_json,
                http_status=code,
                ttfb_ms=ttfb,
                total_latency_ms=total_ms,
                tokens_in=in_t,
                tokens_compressed=comp_t,
                tokens_out=out_t,
                llm_reasoning=reasoning,
                error_message=err,
            )
        elif action == "chat_query":
            t0 = time.perf_counter()
            r = await client.post(
                "/api/v1/chat/query",
                json={"query": q_text, "top_k": 3, "mode": "hybrid"},
                headers=headers,
            )
            ms = (time.perf_counter() - t0) * 1000.0
            last_http_status = r.status_code
            last_query = q_text
            resp_bytes = len(r.content) + 300
            if r.status_code == 200:
                body = r.json()
                reply = body.get("answer", "")
                last_llm_reply = reply
                comp = body.get("compression_stats", {}) or {}
                ev = UxSimEvent(
                    run_id=run_id,
                    user_id=user.backend_user_id,
                    tenant_id=user.tenant_id,
                    step_index=step_idx + 1,
                    action_type="chat_query",
                    query=q_text,
                    llm_reply=reply,
                    context={
                        "egress_bytes": resp_bytes,
                        "compression_stats": comp,
                        "citations": body.get("citations", []),
                    },
                    http_status=200,
                    ttfb_ms=ms,
                    total_latency_ms=ms,
                    tokens_in=int(comp.get("original_tokens", 0)),
                    tokens_compressed=int(comp.get("compressed_tokens", 0)),
                    tokens_out=max(0, len(reply) // 4),
                    llm_reasoning=reasoning,
                )
            else:
                ev = UxSimEvent(
                    run_id=run_id,
                    user_id=user.backend_user_id,
                    tenant_id=user.tenant_id,
                    step_index=step_idx + 1,
                    action_type="chat_query",
                    query=q_text,
                    context={"egress_bytes": resp_bytes},
                    http_status=r.status_code,
                    total_latency_ms=ms,
                    llm_reasoning=reasoning,
                    error_message=r.text[:250],
                )
        elif action == "search_hybrid":
            t0 = time.perf_counter()
            r = await client.post(
                "/api/v1/search/hybrid",
                json={"query": q_text, "top_k": 3},
                headers=headers,
            )
            ms = (time.perf_counter() - t0) * 1000.0
            last_http_status = r.status_code
            last_query = q_text
            resp_bytes = len(r.content) + 300
            ctx_json: Dict[str, Any] = r.json() if r.status_code == 200 and isinstance(r.json(), dict) else {}
            if not ctx_json and r.status_code == 200:
                ctx_json = {"results": r.json()}
            ctx_json["egress_bytes"] = resp_bytes
            ev = UxSimEvent(
                run_id=run_id,
                user_id=user.backend_user_id,
                tenant_id=user.tenant_id,
                step_index=step_idx + 1,
                action_type="search_hybrid",
                query=q_text,
                context=ctx_json,
                http_status=r.status_code,
                ttfb_ms=ms,
                total_latency_ms=ms,
                llm_reasoning=reasoning,
                error_message=None if r.status_code == 200 else r.text[:250],
            )
        elif action == "submit_feedback" and last_query and last_llm_reply:
            rating = decision.get("feedback_rating") or "THUMBS_UP"
            if rating not in ("THUMBS_UP", "THUMBS_DOWN"):
                rating = "THUMBS_UP"
            t0 = time.perf_counter()
            r = await client.post(
                "/api/v1/chat/feedback",
                json={
                    "query": last_query,
                    "answer": last_llm_reply[:1500],
                    "rating": rating,
                    "comment": reasoning,
                },
                headers=headers,
            )
            ms = (time.perf_counter() - t0) * 1000.0
            last_http_status = r.status_code
            ev = UxSimEvent(
                run_id=run_id,
                user_id=user.backend_user_id,
                tenant_id=user.tenant_id,
                step_index=step_idx + 1,
                action_type="submit_feedback",
                query=last_query,
                llm_reply=last_llm_reply[:500],
                context={"egress_bytes": len(r.content) + 300},
                feedback_rating=rating,
                http_status=r.status_code,
                ttfb_ms=ms,
                total_latency_ms=ms,
                llm_reasoning=reasoning,
                error_message=None if r.status_code in (200, 201) else r.text[:250],
            )
        else:
            t0 = time.perf_counter()
            r = await client.get("/api/v1/documents", headers=headers)
            ms = (time.perf_counter() - t0) * 1000.0
            last_http_status = r.status_code
            ev = UxSimEvent(
                run_id=run_id,
                user_id=user.backend_user_id,
                tenant_id=user.tenant_id,
                step_index=step_idx + 1,
                action_type="list_documents",
                context={"egress_bytes": len(r.content) + 300},
                http_status=r.status_code,
                ttfb_ms=ms,
                total_latency_ms=ms,
                llm_reasoning=reasoning,
                error_message=None if r.status_code == 200 else r.text[:250],
            )

        await storage.record_event(ev)
        events.append(ev)

    return events
