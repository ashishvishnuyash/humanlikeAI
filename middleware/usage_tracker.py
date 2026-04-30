"""LLM usage tracker (Postgres).

Writes one row to ``usage_logs`` per LLM call and atomically updates the
caller's company credit balance + alert state. Fire-and-forget — runs in a
daemon thread so the request path is never blocked.

Token-extraction helpers cover both the direct OpenAI client and LangChain's
``with_structured_output(include_raw=True)`` pattern.

Cost rates come from env vars (``COST_PER_1K_INPUT_TOKENS``,
``COST_PER_1K_OUTPUT_TOKENS``) so we can tune pricing per provider/model
without redeploying code.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Optional, Tuple

from db.models import UsageLog
from db.session import get_session_factory


# ─── Cost helper ──────────────────────────────────────────────────────────────


def _calc_cost(tokens_in: int, tokens_out: int) -> float:
    rate_in = float(os.environ.get("COST_PER_1K_INPUT_TOKENS", "0.00015"))
    rate_out = float(os.environ.get("COST_PER_1K_OUTPUT_TOKENS", "0.0006"))
    return round((tokens_in / 1000 * rate_in) + (tokens_out / 1000 * rate_out), 8)


# ─── Token extraction helpers ─────────────────────────────────────────────────


def tokens_from_openai_completion(completion) -> Tuple[int, int]:
    """Return ``(tokens_in, tokens_out)`` from a direct openai.chat.completions response."""
    try:
        usage = completion.usage
        return int(usage.prompt_tokens), int(usage.completion_tokens)
    except Exception:
        return 0, 0


def tokens_from_langchain_raw(raw_message) -> Tuple[int, int]:
    """Extract tokens from a LangChain AIMessage (``with_structured_output(include_raw=True)['raw']``)."""
    try:
        meta = getattr(raw_message, "usage_metadata", None) or {}
        return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
    except Exception:
        return 0, 0


# ─── company_id coercion ──────────────────────────────────────────────────────


def _to_uuid(value) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# ─── Core tracker ─────────────────────────────────────────────────────────────


def track_usage(
    user_id: str,
    company_id: str,
    feature: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    db=None,  # accepted for backward-compat with the Firestore-era signature
    provider: Optional[str] = None,
    latency_ms: int = 0,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Fire-and-forget: insert a usage_log row and update company credits."""
    if tokens_in == 0 and tokens_out == 0:
        return

    _provider = provider or os.environ.get("AI_PROVIDER", "openai")
    cost = _calc_cost(tokens_in, tokens_out)
    company_uuid = _to_uuid(company_id)

    def _write():
        try:
            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                session.add(
                    UsageLog(
                        user_id=user_id or None,
                        company_id=company_uuid,
                        feature=feature,
                        model=model,
                        provider=_provider,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        total_tokens=tokens_in + tokens_out,
                        estimated_cost_usd=cost,
                        latency_ms=latency_ms,
                        success=success,
                        error=error,
                    )
                )
                session.commit()

            # Update company credits + trigger alerts (skip for anonymous).
            if company_uuid is not None and cost > 0:
                try:
                    from utils.credit_manager import update_company_credits
                    from utils.credit_alerts import check_and_alert

                    updated = update_company_credits(company_uuid, cost)
                    if updated:
                        check_and_alert(company_uuid, updated)
                except Exception as ce:
                    print(f"[usage_tracker] credit update error: {ce}")
        except Exception as e:
            print(f"[usage_tracker] write error ({feature}): {e}")

    threading.Thread(target=_write, daemon=True).start()
