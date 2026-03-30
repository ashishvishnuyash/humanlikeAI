from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from firebase_admin import auth as fb_auth
from firebase_config import get_db
from routers.auth import get_current_user

router = APIRouter(tags=["Users & Hierarchy"], dependencies=[Depends(get_current_user)])

class CreateEmployeeRequest(BaseModel):
    email: str
    password: str
    firstName: str
    lastName: str
    role: str
    department: Optional[str] = ""
    position: Optional[str] = ""
    company_id: str
    managerId: Optional[str] = None
    hierarchyLevel: Optional[int] = 0
    permissions: Optional[Dict[str, bool]] = {}

class HierarchyTestPost(BaseModel):
    userId: str
    targetUserId: str
    companyId: str

class CreateEmployeeResponse(BaseModel):
    success: bool
    uid: str

class HierarchyTestGetResponse(BaseModel):
    success: bool
    userId: str
    companyId: str
    testType: str
    results: dict

class HierarchyTestPostResponse(BaseModel):
    success: bool
    canAccess: bool
    userId: str
    targetUserId: str
    message: str

@router.post("/createEmployee", response_model=CreateEmployeeResponse)
async def create_employee(req: CreateEmployeeRequest):
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
        
    try:
        user_record = fb_auth.create_user(
            email=req.email,
            password=req.password,
            display_name=f"{req.firstName} {req.lastName}"
        )
        uid = user_record.uid
        
        db = get_db()
        manager_id = req.managerId if req.managerId and req.managerId != "none" else None
        
        doc_data = {
            "id": uid,
            "email": req.email,
            "role": req.role,
            "first_name": req.firstName,
            "last_name": req.lastName,
            "department": req.department,
            "position": req.position,
            "company_id": req.company_id,
            "manager_id": manager_id,
            "hierarchy_level": req.hierarchyLevel,
            "direct_reports": [],
            "reporting_chain": [],
            "is_active": True,
            "created_at": SERVER_TIMESTAMP,
            "updated_at": SERVER_TIMESTAMP
        }
        if req.permissions:
            doc_data.update(req.permissions)
            
        db.collection('users').document(uid).set(doc_data)
        
        return {"success": True, "uid": uid}
        
    except Exception as e:
        error_msg = str(e)
        if "EMAIL_EXISTS" in error_msg:
            raise HTTPException(409, "Email already exists")
        raise HTTPException(500, detail=str(e))

@router.get("/hierarchy/test", response_model=HierarchyTestGetResponse)
async def test_hierarchy_get(userId: str, companyId: str, testType: str = "all"):
    # Mocking hierarchy tests for python migration
    return {
        "success": True,
        "userId": userId,
        "companyId": companyId,
        "testType": testType,
        "results": {"message": "Tests migrated to Python stub"}
    }
    
@router.post("/hierarchy/test", response_model=HierarchyTestPostResponse)
async def test_hierarchy_post(req: HierarchyTestPost):
    # Mocking check access permissions
    return {
        "success": True,
        "canAccess": True,
        "userId": req.userId,
        "targetUserId": req.targetUserId,
        "message": "User has access to target employee data (mocked)"
    }
