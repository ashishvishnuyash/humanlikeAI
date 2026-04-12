from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal, Annotated, Union, List, Dict
from datetime import datetime
import random
import string
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.transforms import Increment
from firebase_config import get_db
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

def get_or_create_anonymous_profile(employee_id: str, company_id: str):
    db = get_db()
    profiles_ref = db.collection('anonymous_profiles')
    docs = profiles_ref.where('employee_id', '==', employee_id).where('company_id', '==', company_id).stream()
    
    for doc in docs:
        d = doc.to_dict()
        d['id'] = doc.id
        return d
        
    anonymous_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#DDA15E', '#BC6C25', '#FFB5A7']
    avatar_color = random.choice(colors)
    display_name = f"User {anonymous_id}"
    
    new_profile = {
        'employee_id': employee_id,
        'company_id': company_id,
        'anonymous_id': anonymous_id,
        'display_name': display_name,
        'avatar_color': avatar_color,
        'created_at': SERVER_TIMESTAMP,
        'last_active': SERVER_TIMESTAMP
    }
    _, doc_ref = profiles_ref.add(new_profile)
    new_profile['id'] = doc_ref.id
    return new_profile

@router.post("/community", response_model=GenericCommunityResponse)
async def handle_community(req: CommunityRequest):
    db = get_db()
    
    if req.action == 'get_posts':
        posts_ref = db.collection('community_posts')
        docs = posts_ref.where('company_id', '==', req.company_id).where('is_approved', '==', True).stream()
        posts = []
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            posts.append(d)
            
        category = req.data.get('category') if req.data else None
        if category and category != 'all':
            posts = [p for p in posts if p.get('category') == category]
            
        def sort_key(p):
            is_pinned = 1 if p.get('is_pinned') else 0
            # Just approximation for timestamp
            created = 0
            if 'created_at' in p:
                if hasattr(p['created_at'], 'timestamp'):
                    created = p['created_at'].timestamp()
            return (is_pinned, created)
            
        posts.sort(key=sort_key, reverse=True)
        limit_count = req.data.get('limit_count', 20) if req.data else 20
        return {"action": "get_posts", "success": True, "posts": posts[:limit_count]}
        
    elif req.action == 'get_anonymous_profile':
        if not req.employee_id:
            raise HTTPException(400, "employee_id required")
        prof = get_or_create_anonymous_profile(req.employee_id, req.company_id)
        return {"action": "get_anonymous_profile", "success": True, "profile": prof}
        
    elif req.action == 'create_post':
        if not req.employee_id: raise HTTPException(400, "employee_id required")
        prof = get_or_create_anonymous_profile(req.employee_id, req.company_id)
        post = {
            'company_id': req.company_id,
            'author_id': prof.get('anonymous_id'),
            'title': req.data.get('title'),
            'content': req.data.get('content'),
            'category': req.data.get('category', 'general'),
            'tags': req.data.get('tags', []),
            'is_anonymous': True,
            'likes': 0,
            'replies': 0,
            'views': 0,
            'is_pinned': False,
            'is_approved': True,
            'created_at': SERVER_TIMESTAMP,
            'updated_at': SERVER_TIMESTAMP
        }
        _, ref = db.collection('community_posts').add(post)
        post['id'] = ref.id
        return {"action": "create_post", "success": True, "post_id": ref.id, "post": post}
        
    elif req.action == 'get_replies':
        pid = req.data.get('post_id')
        docs = db.collection('community_replies').where('post_id', '==', pid).where('is_approved', '==', True).stream()
        replies = []
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            replies.append(d)
        # ascending order 
        replies.sort(key=lambda x: x.get('created_at').timestamp() if hasattr(x.get('created_at'), 'timestamp') else 0)
        return {"action": "get_replies", "success": True, "replies": replies}
        
    elif req.action == 'create_reply':
        if not req.employee_id: raise HTTPException(400, "employee_id required")
        prof = get_or_create_anonymous_profile(req.employee_id, req.company_id)
        reply = {
            'post_id': req.data.get('post_id'),
            'author_id': prof.get('anonymous_id'),
            'content': req.data.get('content'),
            'is_anonymous': True,
            'likes': 0,
            'is_approved': True,
            'created_at': SERVER_TIMESTAMP,
            'updated_at': SERVER_TIMESTAMP
        }
        _, ref = db.collection('community_replies').add(reply)
        db.collection('community_posts').document(reply['post_id']).update({'replies': Increment(1)})
        reply['id'] = ref.id
        return {"action": "create_reply", "success": True, "reply_id": ref.id, "reply": reply}
        
    elif req.action == 'like_post':
        pid = req.data.get('post_id')
        db.collection('community_posts').document(pid).update({'likes': Increment(1)})
        return {"action": "like_post", "success": True, "message": "Post liked successfully"}
        
    raise HTTPException(400, "Invalid action")


# --- Gamification Logic ---

def get_or_create_user_stats(employee_id, company_id):
    db = get_db()
    ref = db.collection('user_gamification')
    docs = ref.where('employee_id', '==', employee_id).where('company_id', '==', company_id).stream()
    for doc in docs:
        d = doc.to_dict()
        d['id'] = doc.id
        return d
        
    new_stats = {
        'employee_id': employee_id,
        'company_id': company_id,
        'current_streak': 0,
        'longest_streak': 0,
        'total_points': 0,
        'level': 1,
        'badges': [],
        'challenges_completed': 0,
        'last_check_in': None,
        'weekly_goal': 5,
        'monthly_goal': 20,
        'created_at': SERVER_TIMESTAMP,
        'updated_at': SERVER_TIMESTAMP
    }
    _, doc_ref = ref.add(new_stats)
    new_stats['id'] = doc_ref.id
    return new_stats

def calculate_level(pts):
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

def check_badges(stats):
    b = set(stats.get('badges', []))
    new_b = []
    pts = stats.get('total_points', 0)
    streak = stats.get('current_streak', 0)
    lvl = stats.get('level', 1)
    
    if 'first_check_in' not in b and pts > 0: new_b.append('first_check_in')
    if 'week_warrior' not in b and streak >= 7: new_b.append('week_warrior')
    if 'month_master' not in b and streak >= 30: new_b.append('month_master')
    if 'century_streak' not in b and streak >= 100: new_b.append('century_streak')
    if 'point_collector' not in b and pts >= 1000: new_b.append('point_collector')
    if 'point_master' not in b and pts >= 5000: new_b.append('point_master')
    if 'level_five' not in b and lvl >= 5: new_b.append('level_five')
    if 'level_ten' not in b and lvl >= 10: new_b.append('level_ten')
    return new_b


@router.post("/gamification", response_model=GenericGamificationResponse)
async def handle_gamification(req: GamificationRequest):
    db = get_db()
    
    if req.action == 'get_user_stats':
        return {"action": "get_user_stats", "success": True, "user_stats": get_or_create_user_stats(req.employee_id, req.company_id)}
        
    elif req.action == 'check_in':
        stats = get_or_create_user_stats(req.employee_id, req.company_id)
        now = datetime.utcnow()
        last_check_in = stats.get('last_check_in')
        
        last_dt = None
        if last_check_in is not None:
             if hasattr(last_check_in, 'timestamp'):
                 last_dt = datetime.fromtimestamp(last_check_in.timestamp())
                 
        streak = stats.get('current_streak', 0)
        pts = 10
        
        if last_dt:
            hours = (now - last_dt).total_seconds() / 3600
            if hours > 48:
                streak = 1
            elif hours <= 24:
                return {"action": "check_in", "success": False, "message": "You have already checked in today. Come back tomorrow!"}
            else:
                streak += 1
        else:
            streak = 1
            pts = 20
            
        longest = max(streak, stats.get('longest_streak', 0))
        new_pts = stats.get('total_points', 0) + pts
        new_lvl = calculate_level(new_pts)
        
        upd = {
            'current_streak': streak,
            'longest_streak': longest,
            'total_points': new_pts,
            'level': new_lvl,
            'last_check_in': SERVER_TIMESTAMP,
            'updated_at': SERVER_TIMESTAMP
        }
        db.collection('user_gamification').document(stats['id']).update(upd)
        
        stats.update(upd)
        new_badges = check_badges(stats)
        if new_badges:
            b = stats.get('badges', []) + new_badges
            db.collection('user_gamification').document(stats['id']).update({'badges': b})
            stats['badges'] = b
            
        return {"action": "check_in", "success": True, "user_stats": stats, "new_badges": new_badges, "points_earned": pts, "message": f"Check-in recorded! You earned {pts} points!"}

    elif req.action == 'conversation_complete':
        stats = get_or_create_user_stats(req.employee_id, req.company_id)
        pts = 15
        if req.data and req.data.get('type') == 'challenge_complete':
            pts = 50
            
        new_pts = stats.get('total_points', 0) + pts
        new_lvl = calculate_level(new_pts)
        
        upd = {'total_points': new_pts, 'level': new_lvl, 'updated_at': SERVER_TIMESTAMP}
        db.collection('user_gamification').document(stats['id']).update(upd)
        stats.update(upd)
        
        new_badges = check_badges(stats)
        if new_badges:
            b = stats.get('badges', []) + new_badges
            db.collection('user_gamification').document(stats['id']).update({'badges': b})
            stats['badges'] = b
            
        return {"action": "conversation_complete", "success": True, "user_stats": stats, "new_badges": new_badges, "points_earned": pts, "message": f"Conversation complete! You earned {pts} points!"}
        
    elif req.action == 'get_available_challenges':
        res = db.collection('wellness_challenges').where('company_id', '==', req.company_id).where('is_active', '==', True).limit(10).stream()
        challenges = []
        for d in res:
            c = d.to_dict()
            c['id'] = d.id
            challenges.append(c)
        return {"action": "get_available_challenges", "success": True, "challenges": challenges}
        
    elif req.action == 'join_challenge':
        return {"action": "join_challenge", "success": True, "message": "Challenge joined successfully"}
        
    raise HTTPException(400, "Invalid action")
