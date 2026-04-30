"""Shared helpers for awarding points and writing gamification_events (Postgres).

Called from feature endpoints after qualifying user actions:
    Daily check-in       award_points(event_type="daily_checkin",     points=10)
    Conversation done    award_points(event_type="conversation",       points=15)
    Physical check-in    award_points(event_type="physical_checkin",  points=10)
    Challenge complete   award_points(event_type="challenge_complete", points=50)
    Badge unlocked       award_points(event_type="badge_unlock",       points=0)

Fire-and-forget — runs in a daemon thread so the request path is never blocked.

Tables touched:
    user_gamification    per-user stats (one row per user_id; extras JSONB
                         holds longest_streak, last_check_in, weekly_goal,
                         monthly_goal, challenges_completed)
    gamification_events  immutable point-award log
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from db.models import GamificationEvent, UserGamification
from db.session import get_session_factory


# ─── Level thresholds (mirrors community_gamification.py) ─────────────────────


def calculate_level(pts: int) -> int:
    if pts < 100:
        return 1
    if pts < 300:
        return 2
    if pts < 600:
        return 3
    if pts < 1000:
        return 4
    if pts < 1500:
        return 5
    if pts < 2100:
        return 6
    if pts < 2800:
        return 7
    if pts < 3600:
        return 8
    if pts < 4500:
        return 9
    if pts < 5500:
        return 10
    return 10 + int((pts - 5500) / 1500)


def _check_new_badges(stats: UserGamification) -> List[str]:
    existing = set(stats.badges or [])
    new_badges: List[str] = []
    pts = stats.points or 0
    streak = stats.streak or 0
    lvl = stats.level or 1

    candidates = [
        ("first_check_in", pts > 0),
        ("week_warrior", streak >= 7),
        ("month_master", streak >= 30),
        ("century_streak", streak >= 100),
        ("point_collector", pts >= 1000),
        ("point_master", pts >= 5000),
        ("level_five", lvl >= 5),
        ("level_ten", lvl >= 10),
    ]
    for badge, earned in candidates:
        if earned and badge not in existing:
            new_badges.append(badge)
    return new_badges


def _to_uuid(value) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# ─── Main helper ──────────────────────────────────────────────────────────────


def award_points(
    employee_id: str,
    company_id: str,
    event_type: str,
    points: int,
    db=None,  # accepted for backward-compat with the Firestore-era signature
    metadata: Optional[dict] = None,
) -> None:
    """Fire-and-forget: award points, update level/badges, write event log row."""
    if not employee_id or not company_id:
        return

    company_uuid = _to_uuid(company_id)
    if company_uuid is None:
        return

    def _write():
        try:
            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                stats = (
                    session.query(UserGamification)
                    .filter(UserGamification.user_id == employee_id)
                    .one_or_none()
                )
                if stats is None:
                    stats = UserGamification(
                        id=uuid.uuid4(),
                        user_id=employee_id,
                        company_id=company_uuid,
                        points=0,
                        level=1,
                        streak=0,
                        badges=[],
                        extras={
                            "longest_streak": 0,
                            "challenges_completed": 0,
                            "last_check_in": None,
                            "weekly_goal": 5,
                            "monthly_goal": 20,
                        },
                    )
                    session.add(stats)
                    session.flush()

                if points > 0:
                    stats.points = (stats.points or 0) + points
                    stats.level = calculate_level(stats.points)
                    new_badges = _check_new_badges(stats)
                    if new_badges:
                        stats.badges = [*stats.badges, *new_badges]

                session.add(
                    GamificationEvent(
                        id=uuid.uuid4(),
                        user_id=employee_id,
                        company_id=company_uuid,
                        event_type=event_type,
                        points=points,
                        event_metadata=metadata or {},
                    )
                )
                session.commit()
        except Exception as e:
            print(f"[gamification_utils] award_points error ({event_type}): {e}")

    threading.Thread(target=_write, daemon=True).start()
