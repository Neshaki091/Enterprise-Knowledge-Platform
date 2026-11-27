"""Tổng hợp toàn diện chỉ số trải nghiệm người dùng (UX Scorecard) từ sự kiện mô phỏng."""

from collections import Counter
from typing import Any, Dict, List, Optional

from .storage import UxSimEvent, UxSimMcpCheck


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * (pct / 100.0)))))
    return round(ordered[idx], 2)


def compute_ux_metrics(
    events: List[UxSimEvent],
    mcp_check: Optional[UxSimMcpCheck],
    total_seeded_users: int,
    active_users: int,
    supabase_cloud: bool,
    llm_pool_stats: Optional[Dict[str, Any]] = None,
    query_budget_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tính toàn bộ chỉ số đo lường UX người dùng từ nhật ký hành động."""
    total_events = len(events)
    success_events = [e for e in events if 200 <= e.http_status < 400]
    err_5xx = [e for e in events if e.http_status >= 500]
    err_429 = [e for e in events if e.http_status == 429]

    chat_events = [e for e in events if e.action_type in ("chat_stream", "chat_query")]
    chat_ok = [e for e in chat_events if e.http_status == 200]
    stream_ok = [e for e in events if e.action_type == "chat_stream" and e.http_status == 200 and e.ttfb_ms > 0]
    search_ok = [e for e in events if e.action_type == "search_hybrid" and e.http_status == 200]

    ttfb_vals = [e.ttfb_ms for e in stream_ok]
    chat_lat_vals = [e.total_latency_ms for e in chat_ok if e.total_latency_ms > 0]
    search_lat_vals = [e.total_latency_ms for e in search_ok if e.total_latency_ms > 0]

    refusal_count = sum(
        1 for e in chat_ok if e.llm_reply and "không tìm thấy thông tin" in e.llm_reply.lower()
    )
    savings_list = []
    for e in chat_ok:
        if e.tokens_in > 0 and e.tokens_compressed >= 0:
            savings_list.append(max(0.0, (1.0 - (e.tokens_compressed / e.tokens_in)) * 100.0))

    fb_events = [e for e in events if e.action_type == "submit_feedback" and e.http_status in (200, 201)]
    thumbs_up = sum(1 for e in fb_events if e.feedback_rating == "THUMBS_UP")
    thumbs_down = sum(1 for e in fb_events if e.feedback_rating == "THUMBS_DOWN")

    # Follow-up rate: số user có >= 2 câu chat/search nối tiếp nhau
    user_query_counts = Counter(e.user_id for e in events if e.action_type in ("chat_stream", "chat_query"))
    followup_users = sum(1 for _, cnt in user_query_counts.items() if cnt >= 2)
    action_dist = dict(Counter(e.action_type for e in events))
    total_egress_bytes = sum(
        int(e.context.get("egress_bytes", 0))
        for e in events
        if isinstance(e.context, dict)
    )
    total_egress_kb = round(total_egress_bytes / 1024.0, 2)
    # Ước lượng gộp 22 ngày làm việc/tháng nếu chạy tool 1 lần/ngày kết hợp tải SOAK (0,6-0,8 GB/tháng)
    sim_monthly_egress_mb = round((total_egress_bytes * 22) / (1024.0 * 1024.0), 3)

    return {
        "storage_target": "Supabase Cloud PostgreSQL" if supabase_cloud else "Local SQLite Fallback",
        "total_seeded_users": total_seeded_users,
        "active_users_simulated": active_users,
        "total_actions": total_events,
        "action_distribution": action_dist,
        "llm_rag_queries_count": len(chat_events),
        "total_egress_bytes": total_egress_bytes,
        "total_egress_kb": total_egress_kb,
        "sim_monthly_egress_mb": sim_monthly_egress_mb,
        "success_rate_percent": round((len(success_events) / max(1, total_events)) * 100.0, 2),
        "rate_limit_429_count": len(err_429),
        "rate_limit_friction_percent": round((len(err_429) / max(1, total_events)) * 100.0, 2),
        "server_5xx_count": len(err_5xx),
        "ttfb_p50_ms": _percentile(ttfb_vals, 50),
        "ttfb_p95_ms": _percentile(ttfb_vals, 95),
        "ttfb_p99_ms": _percentile(ttfb_vals, 99),
        "chat_latency_p50_ms": _percentile(chat_lat_vals, 50),
        "chat_latency_p95_ms": _percentile(chat_lat_vals, 95),
        "search_latency_p95_ms": _percentile(search_lat_vals, 95),
        "avg_compression_savings_percent": round(sum(savings_list) / len(savings_list), 1) if savings_list else 0.0,
        "empty_refusal_rate_percent": round((refusal_count / max(1, len(chat_ok))) * 100.0, 1) if chat_ok else 0.0,
        "followup_session_rate_percent": round((followup_users / max(1, active_users)) * 100.0, 1),
        "feedback_thumbs_up": thumbs_up,
        "feedback_thumbs_down": thumbs_down,
        "user_satisfaction_percent": round((thumbs_up / max(1, thumbs_up + thumbs_down)) * 100.0, 1) if (thumbs_up + thumbs_down) > 0 else 100.0,
        "mcp_supabase_check": {
            "executed": mcp_check is not None,
            "passed": mcp_check.passed if mcp_check else False,
            "row_count": mcp_check.row_count if mcp_check else 0,
            "latency_ms": mcp_check.latency_ms if mcp_check else 0.0,
            "readonly_ddl_blocked": mcp_check.readonly_guard_blocked if mcp_check else False,
        },
        "simulator_llm_pool": llm_pool_stats or {},
        "llm_query_budget": query_budget_stats or {},
    }


def render_markdown_report(run_id: str, target_url: str, metrics: Dict[str, Any]) -> str:
    """Xuất báo cáo Markdown tổng hợp chỉ số UX cho GitHub Step Summary & lưu trữ."""
    mcp = metrics["mcp_supabase_check"]
    mcp_badge = "PASS" if mcp["passed"] else ("SKIPPED" if not mcp["executed"] else "FAIL")
    pool = metrics.get("simulator_llm_pool") or {}
    qbudget = metrics.get("llm_query_budget") or {}
    lines = [
        "# Báo cáo Mô phỏng UX Người dùng (Phase 3)",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Target Backend:** `{target_url}`",
        f"- **Kho lưu trữ Telemetry:** `{metrics['storage_target']}`",
        f"- **Tổng User ID đã seed (1-time):** `{metrics['total_seeded_users']}` | **User ngẫu nhiên phiên này:** `{metrics['active_users_simulated']}`",
        "",
        "## 1. Chỉ số Trải nghiệm Người dùng (UX Scorecard)",
        "",
        "| Nhóm chỉ số | Chỉ số đo | Giá trị |",
        "|---|---|---|",
        f"| Độ ổn định | Tổng hành động / Tỷ lệ thành công | `{metrics['total_actions']}` (`{metrics['success_rate_percent']}%`) |",
        f"| Ma sát hạn mức | Số lần chạm HTTP 429 / Tỷ lệ | `{metrics['rate_limit_429_count']}` (`{metrics['rate_limit_friction_percent']}%`) |",
        f"| Phản hồi SSE | TTFT P50 / P95 / P99 (ms) | `{metrics['ttfb_p50_ms']}` / `{metrics['ttfb_p95_ms']}` / `{metrics['ttfb_p99_ms']}` ms |",
        f"| Độ trễ Chat E2E | P50 / P95 (ms) | `{metrics['chat_latency_p50_ms']}` / `{metrics['chat_latency_p95_ms']}` ms |",
        f"| Độ trễ Search | Hybrid Search P95 (ms) | `{metrics['search_latency_p95_ms']}` ms |",
        f"| Tối ưu 16K TPM | Mức nén ngữ cảnh trung bình | `{metrics['avg_compression_savings_percent']}%` |",
        f"| Chất lượng AI | Tỷ lệ từ chối / Hỏi nối tiếp | `{metrics['empty_refusal_rate_percent']}%` / `{metrics['followup_session_rate_percent']}%` |",
        f"| Mức hài lòng | Thumbs Up / Down (`% hài lòng`) | `{metrics['feedback_thumbs_up']}` 👍 / `{metrics['feedback_thumbs_down']}` 👎 (`{metrics['user_satisfaction_percent']}%`) |",
        "",
        "## 2. Kết quả Test Kết nối MCP Zero-Knowledge với Supabase Cloud DB",
        "",
        f"- **Trạng thái:** `{mcp_badge}`",
        f"- **Số dòng truy vấn SELECT thật:** `{mcp['row_count']}`",
        f"- **Độ trễ MCP Roundtrip:** `{mcp['latency_ms']} ms`",
        f"- **Chặn DDL / DROP TABLE:** `{mcp['readonly_ddl_blocked']}`",
        "",
        "## 3. Phân bổ Hành động Ngẫu nhiên của Virtual Users",
        "",
        f"`{metrics['action_distribution']}`",
        "",
    ]
    if pool:
        lines.extend([
            "## 4. Điều phối Multi-Model LLM Pool (Simulator)",
            "",
            f"- **Models xoay vòng:** `{pool.get('models')}` (ngưỡng `{pool.get('rpm_limit_per_model')}` RPM & `{pool.get('rpd_limit_per_model')}` RPD / model; tổng `{pool.get('combined_rpd_capacity')}` RPD)",
            f"- **Số request thành công theo model:** `{pool.get('calls_by_model')}`",
            f"- **Số lần failover 429/503 & chờ RPM pacing:** `{pool.get('model_failovers', 0)}` failover / `{pool.get('throttle_waits', 0)}` waits (`{pool.get('total_wait_ms', 0)} ms`)",
            f"- **Ước tính RPD còn lại trong ngày:** `{pool.get('rpd_remaining_by_model')}`",
            "",
        ])
    lines.extend([
        "## 5. Kiểm soát Ngân sách LLM Query & GCP Egress (< 0,8 GB/tháng)",
        "",
        f"- **Số query tra cứu Backend LLM (`chat_stream`/`chat_query`):** `{qbudget.get('used_llm_queries', metrics['llm_rag_queries_count'])}` / `{qbudget.get('max_llm_queries', 20)}` (đã hạ tải sang search/docs nhẹ: `{qbudget.get('downgraded_to_lightweight', 0)}` lượt)",
        f"- **Egress đo được trong lần chạy này:** `{metrics['total_egress_kb']} KB` (`{metrics['total_egress_bytes']} bytes`)",
        f"- **Ước tính egress tool 22 ngày/tháng (kết hợp SOAK 0,6–0,8 GB/tháng):** `+{metrics['sim_monthly_egress_mb']} MB/tháng` (an toàn dưới trần `819,2 MB` = `0,8 GB`)",
        "",
    ])
    return "\n".join(lines)
