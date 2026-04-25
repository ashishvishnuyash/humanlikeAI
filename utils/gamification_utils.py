"""
Gamification Utilities
=======================
Shared helpers for awarding points and writing gamification_events.

Called from feature endpoints after qualifying user actions:
  - Daily check-in     → award_points(event_type="daily_checkin",     points=10)
  - Conversation done  → award_points(event_type="conversation",       points=15)
  - Physical check-in  → award_points(event_type="physical_checkin",  points=10)
  - Challenge complete → award_points(event_type="challenge_complete", points=50)
  - Badge unlocked     → award_points(event_type="badge_unlock",       points=0)

Fire-and-forget: all writes happen in a daemon thread — never blocks the request path.

Firestore collections touched:
  user_gamification/{doc_id}   — per-user stats (keyed by employee_id + company_id)
  gamification_events/{auto}   — immutable event log (for audit + leaderboard history)
"""

import threading
from datetime import datetime, timezone
from typing import List, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.transforms import Increment


# ─── Level thresholds (mirrors community_gamification.py) ────────────────────

def calculate_level(pts: int) -> int:
    if pts < 100:   return 1
    if pts < 300:   return 2
    if pts < 600:   return 3
    if pts < 1000:  return 4
    if pts < 1500:  return 5
    if pts < 2100:  return 6
    if pts < 2800:  return 7
    if pts < 3600:  return 8
    if pts < 4500:  return 9
    if pts < 5500:  return 10
    return 10 + int((pts - 5500) / 1500)


def _check_new_badges(stats: dict) -> List[str]:
    existing = set(stats.get("badges", []))
    new_badges: List[str] = []
    pts    = stats.get("total_points", 0)
    streak = stats.get("current_streak", 0)
    lvl    = stats.get("level", 1)

    candidates = [
        ("first_check_in",  pts > 0),
        ("week_warrior",    streak >= 7),
        ("month_master",    streak >= 30),
        ("century_streak",  streak >= 100),
        ("point_collector", pts >= 1000),
        ("point_master",    pts >= 5000),
        ("level_five",      lvl >= 5),
        ("level_ten",       lvl >= 10),
    ]
    for badge, earned in candidates:
        if earned and badge not in existing:
            new_badges.append(badge)
    return new_badges


# ─── Main helper ──────────────────────────────────────────────────────────────

def award_points(
    employee_id: str,
    company_id:  str,
    event_type:  str,
    points:      int,
    db,
    metadata:    Optional[dict] = None,
) -> None:
    """
    Fire-and-forget: award `points` to a user, update their level, check badges,
    and write one gamification_events record.

    employee_id — the Firebase UID of the user (called employee_id in user_gamification)
    event_type  — "daily_checkin" | "conversation" | "physical_checkin"
                  | "challenge_complete" | "badge_unlock"
    """
    if not db or not employee_id or not company_id:
        return

    def _write():
        try:
            # ── Find or create user_gamification doc ──────────────────────
            ref_col = db.collection("user_gamification")
            docs    = list(
                ref_col
                .where("employee_id", "==", employee_id)
                .where("company_id",  "==", company_id)
                .limit(1)
                .stream()
            )

            if docs:
                doc_ref = ref_col.document(docs[0].id)
                stats   = docs[0].to_dict()
            else:
                # Lazy creation
                new_stats = {
                    "employee_id":           employee_id,
                    "company_id":            company_id,
                    "current_streak":        0,
                    "longest_streak":        0,
                    "total_points":          0,
                    "level":                 1,
                    "badges":                [],
                    "challenges_completed":  0,
                    "last_check_in":         None,
                    "weekly_goal":           5,
                    "monthly_goal":          20,
                    "created_at":            SERVER_TIMESTAMP,
                    "updated_at":            SERVER_TIMESTAMP,
                }
                _, doc_ref = ref_col.add(new_stats)
                stats = new_stats

            if points > 0:
                new_total = int(stats.get("total_points", 0)) + points
                new_level = calculate_level(new_total)

                doc_ref.update({
                    "total_points": Increment(points),
                    "level":        new_level,
                    "updated_at":   SERVER_TIMESTAMP,
                })

                # Re-read for badge check
                fresh = doc_ref.get().to_dict() or {}
                new_badges = _check_new_badges(fresh)
                if new_badges:
                    current_badges = fresh.get("badges", [])
                    doc_ref.update({"badges": current_badges + new_badges})

            # ── Write gamification_events record ──────────────────────────
            db.collection("gamification_events").add({
                "employee_id": employee_id,
                "company_id":  company_id,
                "event_type":  event_type,
                "points":      points,
                "metadata":    metadata or {},
                "created_at":  SERVER_TIMESTAMP,
            })

        except Exception as e:
            print(f"[gamification_utils] award_points error ({event_type}): {e}")

    threading.Thread(target=_write, daemon=True).start()
