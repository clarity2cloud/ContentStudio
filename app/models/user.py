from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional
from datetime import datetime

# ==================== REQUEST MODELS ====================


class UserSignup(BaseModel):
    """User signup request"""
    email: EmailStr
    password: str = Field(..., min_length=6,
                          description="Password (min 6 characters)")
    full_name: str = Field(..., min_length=1, description="Full name")

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password must be at least 6 characters')
        return v


class UserLogin(BaseModel):
    """User login request"""
    email: EmailStr
    password: str

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()


class OTPVerify(BaseModel):
    """OTP verification request"""
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()


class ResendOTP(BaseModel):
    """Resend OTP request"""
    email: EmailStr

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()


class ForgotPassword(BaseModel):
    """Forgot password request"""
    email: EmailStr

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()


class ResetPassword(BaseModel):
    """Reset password request"""
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6)

    @validator('email')
    def normalize_email(cls, v):
        return v.lower().strip()


class ChangePassword(BaseModel):
    """Change password request"""
    current_password: str
    new_password: str = Field(..., min_length=6)


class UpdateProfile(BaseModel):
    """Update profile request"""
    full_name: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    """Refresh token request"""
    refresh_token: str


# ==================== RESPONSE MODELS ====================

class TokenResponse(BaseModel):
    """Authentication token response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


class UserResponse(BaseModel):
    """User data response"""
    id: str
    email: str
    full_name: Optional[str] = None
    is_verified: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    """Generic message response"""
    message: str
    email: Optional[str] = None
    otp_sent: Optional[bool] = None


# Update forward refs
TokenResponse.model_rebuild()
