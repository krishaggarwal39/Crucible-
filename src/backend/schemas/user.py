import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from backend.db.models.user import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None
    role: UserRole = UserRole.MEMBER


class UserCreate(UserBase):
    password: str
    tenant_id: uuid.UUID


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
