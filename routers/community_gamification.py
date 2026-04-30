from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal, Annotated, Union, List, Dict
from datetime import datetime
import uuid
import random
import string
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.community import (
    AnonymousProfile,
    CommunityPost,
    CommunityReply,
    UserGamification,
    WellnessChallenge,
)
from db.fs_compat import model_to_dict
from routers.auth import get_current_user

router = APIRouter(tags=["Community & Gamification"], dependencies=[Depends(get_current_user)])


class CommunityRequest(BaseModel):
    action: str
    employee_id: Optional[str] = None
    company_id: str
    data: Optional[Any] = {}


class GamificationRequest(BaseModel):
    action: str
    employee_id: str
    company_id: str
    data: Optional[Any] = {}


# --- Community Responses ---
class GetPostsResponse(BaseModel):
    action: Literal["get_posts"]
    success: bool
    posts: List[Dict[str, Any]]


class GetProfileResponse(BaseModel):
    action: Literal["get_anonymous_profile"]
    success: bool
    profile: Dict[str, Any]


class CreatePostResponse(BaseModel):
    action: Literal["create_post"]
    success: bool
    post_id: str
    post: Dict[str, Any]


class GetRepliesResponse(BaseModel):
    action: Literal["get_replies"]
    success: bool
    replies: List[Dict[str, Any]]


class CreateReplyResponse(BaseModel):
    action: Literal["create_reply"]
    success: bool
    reply_id: str
    reply: Dict[str, Any]


class LikePostResponse(BaseModel):
    action: Literal["like_post"]
    success: bool
    message: str


GenericCommunityResponse = Annotated[
    Union[GetPostsResponse, GetProfileResponse, CreatePostResponse, GetRepliesResponse, CreateReplyResponse, LikePostResponse],
    Field(discriminator="action")
]


# --- Gamification Responses ---
class GetStatsResponse(BaseModel):
    action: Literal["get_user_stats"]
    success: bool
    user_stats: Dict[str, Any]


class CheckInResponse(BaseModel):
    action: Literal["check_in"]
    success: bool
    message: str
    user_stats: Optional[Dict[str, Any]] = None
    new_badges: Optional[List[str]] = None
    points_earned: Optional[int] = None


class ConvCompleteResponse(BaseModel):
    action: Literal["conversation_complete"]
    success: bool
    message: str
    user_stats: Dict[str, Any]
    new_badges: List[str]
    points_earned: int


class GetChallengesResponse(BaseModel):
    action: Literal["get_available_challenges"]
    success: bool
    challenges: List[Dict[str, Any]]


class JoinChallengeResponse(BaseModel):
    action: Literal["join_challenge"]
    success: bool
    message: str


GenericGamificationResponse = Annotated[
    Union[GetStatsResponse, CheckInResponse, ConvCompleteResponse, GetChallengesResponse, JoinChallengeResponse],
    Field(discriminator="action")
]


# --- Community Logic ---

def _parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    """Convert a string to UUID, raising HTTP 400 on failure."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value!r}")


def get_or_create_anonymous_profile(employee_id: str, db: Session) -> dict:
    profile = db.query(AnonymousProfile).filter(
        AnonymousProfile.user_id == employee_id
    ).one_or_none()

    if profile is not None:
        return model_to_dict(profile)

    anonymous_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#DDA15E', '#BC6C25', '#FFB5A7']
    avatar_color = random.choice(colors)
    handle = f"User_{anonymous_id}"

    new_profile = AnonymousProfile(
        id=uuid.uuid4(),
        user_id=employee_id,
        handle=handle,
        avatar=avatar_color,
    )
    db.add(new_profile)
    db.commit()
    return model_to_dict(new_profile)


@router.post("/community", response_model=GenericCommunityResponse)
async def handle_community(req: CommunityRequest, db: Session = Depends(get_session)):

    if req.action == 'get_posts':
        company_uuid = _parse_uuid(req.company_id, "company_id")
        rows = (
            db.query(CommunityPost)
            .filter(
                CommunityPost.company_id == company_uuid,
                CommunityPost.is_approved.is_(True),
            )
            .all()
        )
        posts = [model_to_dict(r) for r in rows]

        category = req.data.get('category') if req.data else None
        if category and category != 'all':
            posts = [p for p in posts if p.get('category') == category]

        def sort_key(p):
            is_pinned = 1 if p.get('is_pinned') else 0
            created = 0
            created_val = p.get('created_at')
            if created_val is not None and hasattr(created_val, 'timestamp'):
                created = created_val.timestamp()
            return (is_pinned, created)

        posts.sort(key=sort_key, reverse=True)
        limit_count = req.data.get('limit_count', 20) if req.data else 20
        return {"action": "get_posts", "success": True, "posts": posts[:limit_count]}

    elif req.action == 'get_anonymous_profile':
        if not req.employee_id:
            raise HTTPException(400, "employee_id required")
        prof = get_or_create_anonymous_profile(req.employee_id, db)
        return {"action": "get_anonymous_profile", "success": True, "profile": prof}

    elif req.action == 'create_post':
        if not req.employee_id:
            raise HTTPException(400, "employee_id required")
        company_uuid = _parse_uuid(req.company_id, "company_id")
        prof = get_or_create_anonymous_profile(req.employee_id, db)

        profile_obj = db.query(AnonymousProfile).filter(
            AnonymousProfile.user_id == req.employee_id
        ).one_or_none()

        content = req.data.get('content', '') if req.data else ''
        post = CommunityPost(
            id=uuid.uuid4(),
            company_id=company_uuid,
            anonymous_profile_id=uuid.UUID(prof['id']) if profile_obj else None,
            content=content,
            likes=0,
            replies=0,
            is_approved=True,
        )
        db.add(post)
        db.commit()
        post_dict = model_to_dict(post)
        # Preserve extra fields from request for response shape parity
        post_dict['title'] = req.data.get('title') if req.data else None
        post_dict['category'] = req.data.get('category', 'general') if req.data else 'general'
        post_dict['tags'] = req.data.get('tags', []) if req.data else []
        post_dict['is_anonymous'] = True
        post_dict['views'] = 0
        post_dict['is_pinned'] = False
        return {"action": "create_post", "success": True, "post_id": str(post.id), "post": post_dict}

    elif req.action == 'get_replies':
        pid = req.data.get('post_id') if req.data else None
        if not pid:
            raise HTTPException(400, "post_id required")
        post_uuid = _parse_uuid(str(pid), "post_id")
        rows = (
            db.query(CommunityReply)
            .filter(
                CommunityReply.post_id == post_uuid,
                CommunityReply.is_approved.is_(True),
            )
            .all()
        )
        replies = [model_to_dict(r) for r in rows]
        replies.sort(key=lambda x: x.get('created_at').timestamp() if hasattr(x.get('created_at'), 'timestamp') else 0)
        return {"action": "get_replies", "success": True, "replies": replies}

    elif req.action == 'create_reply':
        if not req.employee_id:
            raise HTTPException(400, "employee_id required")
        prof = get_or_create_anonymous_profile(req.employee_id, db)
        profile_obj = db.query(AnonymousProfile).filter(
            AnonymousProfile.user_id == req.employee_id
        ).one_or_none()

        pid = req.data.get('post_id') if req.data else None
        if not pid:
            raise HTTPException(400, "post_id required")
        post_uuid = _parse_uuid(str(pid), "post_id")

        content = req.data.get('content', '') if req.data else ''
        reply = CommunityReply(
            id=uuid.uuid4(),
            post_id=post_uuid,
            anonymous_profile_id=uuid.UUID(prof['id']) if profile_obj else None,
            content=content,
            is_approved=True,
        )
        db.add(reply)
        db.commit()

        # Increment reply count on the post
        db.query(CommunityPost).filter(CommunityPost.id == post_uuid).update(
            {"replies": CommunityPost.replies + 1}
        )
        db.commit()

        reply_dict = model_to_dict(reply)
        reply_dict['is_anonymous'] = True
        return {"action": "create_reply", "success": True, "reply_id": str(reply.id), "reply": reply_dict}

    elif req.action == 'like_post':
        pid = req.data.get('post_id') if req.data else None
        if not pid:
            raise HTTPException(400, "post_id required")
        post_uuid = _parse_uuid(str(pid), "post_id")
        db.query(CommunityPost).filter(CommunityPost.id == post_uuid).update(
            {"likes": CommunityPost.likes + 1}
        )
        db.commit()
        return {"action": "like_post", "success": True, "message": "Post liked successfully"}

    raise HTTPException(400, "Invalid action")


# --- Gamification Logic ---

def get_or_create_user_stats(employee_id: str, company_id: str, db: Session) -> tuple[UserGamification, bool]:
    """Return (UserGamification obj, created: bool)."""
    ug = db.query(UserGamification).filter(
        UserGamification.user_id == employee_id
    ).one_or_none()

    if ug is not None:
        return ug, False

    try:
        cid = uuid.UUID(company_id)
    except (ValueError, AttributeError):
        cid = None

    ug = UserGamification(
        id=uuid.uuid4(),
        user_id=employee_id,
        company_id=cid,
        points=0,
        level=1,
        streak=0,
        badges=[],
    )
    db.add(ug)
    db.commit()
    return ug, True


def _ug_to_stats_dict(ug: UserGamification) -> dict:
    """Serialize UserGamification to a response-friendly dict."""
    return {
        'id': str(ug.id),
        'user_id': ug.user_id,
        'company_id': str(ug.company_id) if ug.company_id else None,
        'points': ug.points,
        'total_points': ug.points,          # compat alias
        'level': ug.level,
        'badges': list(ug.badges or []),
        'streak': ug.streak,
        'current_streak': ug.streak,        # compat alias
        'updated_at': ug.updated_at,
    }


def calculate_level(pts: int) -> int:
    if pts < 100: return 1
    if pts < 300: return 2
    if pts < 600: return 3
    if pts < 1000: return 4
    if pts < 1500: return 5
    if pts < 2100: return 6
    if pts < 2800: return 7
    if pts < 3600: return 8
    if pts < 4500: return 9
    if pts < 5500: return 10
    return 10 + int((pts - 5500) / 1500)


def check_badges(stats: dict) -> list:
    b = set(stats.get('badges', []))
    new_b = []
    pts = stats.get('points', stats.get('total_points', 0))
    streak = stats.get('streak', stats.get('current_streak', 0))
    lvl = stats.get('level', 1)

    if 'first_check_in' not in b and pts > 0:
        new_b.append('first_check_in')
    if 'week_warrior' not in b and streak >= 7:
        new_b.append('week_warrior')
    if 'month_master' not in b and streak >= 30:
        new_b.append('month_master')
    if 'century_streak' not in b and streak >= 100:
        new_b.append('century_streak')
    if 'point_collector' not in b and pts >= 1000:
        new_b.append('point_collector')
    if 'point_master' not in b and pts >= 5000:
        new_b.append('point_master')
    if 'level_five' not in b and lvl >= 5:
        new_b.append('level_five')
    if 'level_ten' not in b and lvl >= 10:
        new_b.append('level_ten')
    return new_b


@router.post("/gamification", response_model=GenericGamificationResponse)
async def handle_gamification(req: GamificationRequest, db: Session = Depends(get_session)):

    if req.action == 'get_user_stats':
        ug, _ = get_or_create_user_stats(req.employee_id, req.company_id, db)
        return {"action": "get_user_stats", "success": True, "user_stats": _ug_to_stats_dict(ug)}

    elif req.action == 'check_in':
        ug, _ = get_or_create_user_stats(req.employee_id, req.company_id, db)
        now = datetime.utcnow()

        # Use updated_at as a proxy for last check-in time
        last_dt = ug.updated_at if ug.updated_at else None
        streak = ug.streak
        pts = 10

        if last_dt is not None:
            hours = (now - last_dt).total_seconds() / 3600
            if hours > 48:
                streak = 1
            elif hours <= 24:
                return {
                    "action": "check_in",
                    "success": False,
                    "message": "You have already checked in today. Come back tomorrow!",
                }
            else:
                streak += 1
        else:
            streak = 1
            pts = 20

        new_pts = ug.points + pts
        new_lvl = calculate_level(new_pts)

        ug.points = new_pts
        ug.level = new_lvl
        ug.streak = streak
        db.commit()

        stats = _ug_to_stats_dict(ug)
        new_badges = check_badges(stats)
        if new_badges:
            ug.badges = [*ug.badges, *new_badges]
            db.commit()
            stats['badges'] = list(ug.badges)

        return {
            "action": "check_in",
            "success": True,
            "user_stats": stats,
            "new_badges": new_badges,
            "points_earned": pts,
            "message": f"Check-in recorded! You earned {pts} points!",
        }

    elif req.action == 'conversation_complete':
        ug, _ = get_or_create_user_stats(req.employee_id, req.company_id, db)
        pts = 15
        if req.data and req.data.get('type') == 'challenge_complete':
            pts = 50

        new_pts = ug.points + pts
        new_lvl = calculate_level(new_pts)

        ug.points = new_pts
        ug.level = new_lvl
        db.commit()

        stats = _ug_to_stats_dict(ug)
        new_badges = check_badges(stats)
        if new_badges:
            ug.badges = [*ug.badges, *new_badges]
            db.commit()
            stats['badges'] = list(ug.badges)

        return {
            "action": "conversation_complete",
            "success": True,
            "user_stats": stats,
            "new_badges": new_badges,
            "points_earned": pts,
            "message": f"Conversation complete! You earned {pts} points!",
        }

    elif req.action == 'get_available_challenges':
        try:
            company_uuid = uuid.UUID(req.company_id)
        except (ValueError, AttributeError):
            raise HTTPException(400, "Invalid company_id")
        rows = (
            db.query(WellnessChallenge)
            .filter(
                WellnessChallenge.company_id == company_uuid,
                WellnessChallenge.is_active.is_(True),  # noqa: E712
            )
            .limit(10)
            .all()
        )
        challenges = [model_to_dict(r) for r in rows]
        return {"action": "get_available_challenges", "success": True, "challenges": challenges}

    elif req.action == 'join_challenge':
        return {"action": "join_challenge", "success": True, "message": "Challenge joined successfully"}

    raise HTTPException(400, "Invalid action")
