"""
Usage Tracker
==============
Fire-and-forget LLM token + cost logging.

Writes one record to Firestore `usage_logs` per LLM call.
All writes happen in a daemon thread — never blocks the request path.

Usage patterns
--------------

1. Direct OpenAI client calls (chat_wrapper, recommendations):
   Read `completion.usage.prompt_tokens` / `completion.usage.completion_tokens`
   directly from the response object, then call track_usage().

2. LangChain with_structured_output() calls (report_agent, physical_health_agent):
   Pass `include_raw=True` to get back {"raw": AIMessage, "parsed": Pydantic}.
   Read `raw.usage_metadata["input_tokens"]` / `["output_tokens"]`.

Cost calculation
----------------
Token prices are read from env vars so Azure migration doesn't break costs:
  COST_PER_1K_INPUT_TOKENS   (default: 0.00015  — gpt-4o-mini input)
  COST_PER_1K_OUTPUT_TOKENS  (default: 0.0006   — gpt-4o-mini output)
Update these in .env when switching provider or model.

Firestore schema — usage_logs/{auto_id}:
{
    user_id:             str,
    company_id:          str,
    timestamp:           Timestamp,
    feature:             str,    # "chat" | "report" | "recommendation"
                                 # | "physical_health" | "embedding"
    model:               str,    # "gpt-4o-mini" | "gpt-4"
    provider:            str,    # "openai" | "azure"
    tokens_in:           int,
    tokens_out:          int,
    total_tokens:        int,
    estimated_cost_usd:  float,
    latency_ms:          int,
    success:             bool,
    error:               str | null
}
"""

import os
import threading
from typing import Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP


# ─── Cost helper ──────────────────────────────────────────────────────────────

def _calc_cost(tokens_in: int, tokens_out: int) -> float:
    rate_in  = float(os.environ.get("COST_PER_1K_INPUT_TOKENS",  "0.00015"))
    rate_out = float(os.environ.get("COST_PER_1K_OUTPUT_TOKENS", "0.0006"))
    return round((tokens_in / 1000 * rate_in) + (tokens_out / 1000 * rate_out), 8)


# ─── Token extraction helpers ─────────────────────────────────────────────────

def tokens_from_openai_completion(completion) -> tuple[int, int]:
    """
    Extract (tokens_in, tokens_out) from a direct openai.chat.completions response.
    Works with both openai>=1.0 (completion.usage.prompt_tokens) and any wrapper.
    """
    try:
        usage = completion.usage
        return int(usage.prompt_tokens), int(usage.completion_tokens)
    except Exception:
        return 0, 0


def tokens_from_langchain_raw(raw_message) -> tuple[int, int]:
    """
    Extract (tokens_in, tokens_out) from a LangChain AIMessage returned by
    with_structured_output(include_raw=True)["raw"].

    raw_message.usage_metadata looks like:
        {"input_tokens": 412, "output_tokens": 87, "total_tokens": 499}
    """
    try:
        meta = getattr(raw_message, "usage_metadata", None) or {}
        return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
    except Exception:
        return 0, 0


# ─── Core tracker ─────────────────────────────────────────────────────────────

def track_usage(
    user_id:    str,
    company_id: str,
    feature:    str,
    model:      str,
    tokens_in:  int,
    tokens_out: int,
    db,
    provider:   Optional[str] = None,
    latency_ms: int  = 0,
    success:    bool = True,
    error:      Optional[str] = None,
) -> None:
    """
    Fire-and-forget: write one usage_log record to Firestore.
    Spawns a daemon thread — returns immediately, never raises.
    """
    if tokens_in == 0 and tokens_out == 0:
        return   # nothing to log

    _provider = provider or os.environ.get("AI_PROVIDER", "openai")
    cost      = _calc_cost(tokens_in, tokens_out)

    def _write():
        try:
            if not db:
                return

            # 1. Write usage log
            db.collection("usage_logs").add({
                "user_id":            user_id    or "anonymous",
                "company_id":         company_id or "",
                "timestamp":          SERVER_TIMESTAMP,
                "feature":            feature,
                "model":              model,
                "provider":           _provider,
                "tokens_in":          tokens_in,
                "tokens_out":         tokens_out,
                "total_tokens":       tokens_in + tokens_out,
                "estimated_cost_usd": cost,
                "latency_ms":         latency_ms,
                "success":            success,
                "error":              error,
            })

            # 2. Update company credit balance + trigger alerts (skip for anonymous)
            if company_id and cost > 0:
                try:
                    from utils.credit_manager import update_company_credits
                    from utils.credit_alerts import check_and_alert
                    updated = update_company_credits(company_id, cost, db)
                    if updated:
                        check_and_alert(company_id, updated, db)
                except Exception as ce:
                    print(f"[usage_tracker] credit update error: {ce}")

        except Exception as e:
            print(f"[usage_tracker] write error ({feature}): {e}")

    threading.Thread(target=_write, daemon=True).start()
