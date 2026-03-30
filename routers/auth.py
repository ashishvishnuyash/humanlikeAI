import httpx
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
from firebase_admin import auth as fb_auth
from firebase_config import get_db, firebaseConfig
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """FastAPI dependency to verify Firebase JWT tokens."""
    token = credentials.credentials
    try:
        decoded_token = fb_auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

router = APIRouter(prefix="/auth", tags=["Auth"])

class RegisterRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str
    companyName: str
    companySize: Optional[str] = "Not specified"
    industry: Optional[str] = "Not specified"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RegisterResponse(BaseModel):
    message: str
    userId: str

class UserDetails(BaseModel):
    uid: str
    email: str
    displayName: str

class LoginResponse(BaseModel):
    message: str
    access_token: str
    token_type: str
    expires_in: str
    user: UserDetails

class MeResponse(BaseModel):
    message: str
    token_payload: dict
    database_profile: Optional[dict] = None

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
async def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail={"password": ["Password must be at least 6 characters"]})
        
    try:
        # Create user in Firebase Auth
        user_record = fb_auth.create_user(
            email=req.email,
            password=req.password,
            display_name=f"{req.firstName} {req.lastName}"
        )
        
        uid = user_record.uid
        company_id = f"company_{uid}"
        
        db = get_db()
        if db:
            # Create company document
            db.collection("companies").document(company_id).set({
                "id": company_id,
                "name": req.companyName,
                "size": req.companySize,
                "industry": req.industry,
                "owner_id": uid,
                "created_at": SERVER_TIMESTAMP,
                "updated_at": SERVER_TIMESTAMP,
            })
            
            # Create employer user profile
            db.collection("users").document(uid).set({
                "id": uid,
                "email": req.email,
                "first_name": req.firstName,
                "last_name": req.lastName,
                "role": "employer",
                "company_id": company_id,
                "company_name": req.companyName,
                "is_active": True,
                "hierarchy_level": 0,
                "can_view_team_reports": True,
                "can_manage_employees": True,
                "can_approve_leaves": True,
                "is_department_head": True,
                "skip_level_access": True,
                "direct_reports": [],
                "created_at": SERVER_TIMESTAMP,
                "updated_at": SERVER_TIMESTAMP,
            })
            
        return {"message": "Company account created successfully!", "userId": uid}
        
    except Exception as e:
        error_msg = str(e)
        if "EMAIL_EXISTS" in error_msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        print(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    api_key = firebaseConfig.get("apiKey")
    if not api_key:
        raise HTTPException(status_code=500, detail="Server misconfiguration: Missing Firebase API Key.")
        
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={
            "email": req.email,
            "password": req.password,
            "returnSecureToken": True
        })
        
        if resp.status_code != 200:
            error_data = resp.json()
            err_msg = error_data.get("error", {}).get("message", "Incorrect email or password.")
            if err_msg in ["INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD", "EMAIL_NOT_FOUND"]:
                err_msg = "Invalid email or password."
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=err_msg)
            
        data = resp.json()
        return {
            "message": "Login successful!",
            "access_token": data["idToken"],
            "token_type": "bearer",
            "expires_in": data["expiresIn"],
            "user": {
                "uid": data["localId"],
                "email": data["email"],
                "displayName": data.get("displayName", "")
            }
        }

@router.get("/me", response_model=MeResponse)
async def get_me(user_token: dict = Depends(get_current_user)):
    """Example protected endpoint that decodes the JWT and returns the user details."""
    uid = user_token.get("uid")
    db = get_db()
    
    # Try fetching fresh data from Firestore based on the secure JWT UID
    user_doc = db.collection("users").document(uid).get()
    
    return {
        "message": "You are securely authenticated via JWT!", 
        "token_payload": user_token,
        "database_profile": user_doc.to_dict() if user_doc.exists else None
    }
