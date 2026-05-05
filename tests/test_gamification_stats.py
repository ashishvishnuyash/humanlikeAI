"""Verify gamification serializer exposes longest_streak,
challenges_completed, weekly_goal, monthly_goal; and that check_in
updates longest_streak when streak surpasses it."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from db.models.community import UserGamification
from routers.community_gamification import _ug_to_stats_dict


def _make_ug(**kwargs) -> UserGamification:
    defaults = dict(
        id=uuid.uuid4(),
        user_id="u-1",
        company_id=None,
        points=0,
        level=1,
        badges=[],
        streak=0,
        longest_streak=0,
        challenges_completed=0,
        weekly_goal=5,
        monthly_goal=20,
        updated_at=datetime.utcnow(),
    )
    defaults.update(kwargs)
    return UserGamification(**defaults)


def test_stats_dict_exposes_new_fields():
    ug = _make_ug(
        points=120, level=2, streak=4, longest_streak=12,
        challenges_completed=3, weekly_goal=7, monthly_goal=25,
        badges=["first_check_in"],
    )
    out = _ug_to_stats_dict(ug)
    assert out["longest_streak"] == 12
    assert out["challenges_completed"] == 3
    assert out["weekly_goal"] == 7
    assert out["monthly_goal"] == 25
    assert out["points"] == 120
    assert out["total_points"] == 120
    assert out["streak"] == 4
    assert out["current_streak"] == 4


def test_check_in_updates_longest_streak_when_surpassed():
    """check_in should bump longest_streak when current streak grows past it."""
    from routers.community_gamification import handle_gamification, GamificationRequest

    last_dt = datetime.utcnow() - timedelta(hours=25)
    ug = _make_ug(streak=6, longest_streak=6, points=60, updated_at=last_dt)

    session = MagicMock()
    query = MagicMock()
    filt = MagicMock()
    filt.one_or_none.return_value = ug
    query.filter.return_value = filt
    session.query.return_value = query

    import asyncio
    result = asyncio.run(handle_gamification(
        GamificationRequest(action="check_in", employee_id="u-1", company_id=str(uuid.uuid4())),
        db=session,
    ))

    assert result["success"] is True
    assert ug.streak == 7
    assert ug.longest_streak == 7
