"""Verify get_or_create_anonymous_profile scopes by (user_id, company_id),
so the same user in two companies gets two profiles."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from routers.community_gamification import get_or_create_anonymous_profile


def _mock_session_returning(existing):
    """Build a mock SQLAlchemy session whose .query().filter().one_or_none()
    returns `existing` (an object or None)."""
    session = MagicMock()
    query = MagicMock()
    filt = MagicMock()
    filt.one_or_none.return_value = existing
    query.filter.return_value = filt
    session.query.return_value = query
    return session


def test_creates_profile_when_none_exists_for_user_company_pair():
    session = _mock_session_returning(None)
    company_id = str(uuid.uuid4())

    result = get_or_create_anonymous_profile("user-1", company_id, session)

    # Must have called add() to insert a new AnonymousProfile
    assert session.add.called, "expected db.add() to be called for new profile"
    added = session.add.call_args[0][0]
    assert added.user_id == "user-1"
    assert str(added.company_id) == company_id
    assert isinstance(result, dict)
    assert result["user_id"] == "user-1"


def _make_col(name):
    """Build a minimal column-like mock for model_to_dict iteration."""
    col = MagicMock()
    col.name = name
    return col


def test_returns_existing_when_user_company_pair_exists():
    profile_id = uuid.uuid4()
    company_uuid = uuid.UUID("00000000-0000-0000-0000-000000000001")

    existing = MagicMock()
    existing.id = profile_id
    existing.user_id = "user-1"
    existing.company_id = company_uuid
    existing.handle = "User_ABC123"
    existing.avatar = "#FF6B6B"
    existing.extras = {}
    existing.created_at = None

    # Wire up __table__.columns so model_to_dict can iterate column names.
    cols = [
        _make_col("id"),
        _make_col("user_id"),
        _make_col("company_id"),
        _make_col("handle"),
        _make_col("avatar"),
        _make_col("created_at"),
    ]
    existing.__table__ = MagicMock()
    existing.__table__.columns = cols

    session = _mock_session_returning(existing)
    result = get_or_create_anonymous_profile(
        "user-1", "00000000-0000-0000-0000-000000000001", session
    )

    assert not session.add.called, "should not insert when profile exists"
    assert result["user_id"] == "user-1"
    assert result["handle"] == "User_ABC123"


def test_create_post_response_has_author_id():
    """The post dict in the create_post response must include `author_id`
    (the AnonymousProfile.handle) so the frontend can render the post
    without an additional lookup."""
    from routers.community_gamification import _build_post_dict
    from db.models.community import CommunityPost
    import uuid as _uuid

    post = CommunityPost(
        id=_uuid.uuid4(),
        company_id=_uuid.uuid4(),
        anonymous_profile_id=_uuid.uuid4(),
        content="hello",
        likes=0,
        replies=0,
        is_approved=True,
    )
    profile_handle = "User_ABCDEFGH"
    out = _build_post_dict(post, handle=profile_handle, extras={"category": "general", "title": "t", "tags": []})
    assert out["author_id"] == profile_handle
    assert out["category"] == "general"
    assert out["title"] == "t"
    assert out["is_anonymous"] is True
    assert out["views"] == 0
    assert out["is_pinned"] is False
