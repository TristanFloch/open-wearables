from uuid import UUID

from pydantic import BaseModel, SecretStr


class GarminConnectLoginRequest(BaseModel):
    """Request to authenticate with Garmin Connect via personal credentials."""

    email: str
    password: SecretStr
    user_id: UUID


class GarminConnectMFARequest(BaseModel):
    """Request to complete MFA for Garmin Connect login."""

    user_id: UUID
    session_id: str
    mfa_code: str


class GarminConnectLoginResponse(BaseModel):
    """Response from Garmin Connect authentication."""

    success: bool
    requires_mfa: bool = False
    session_id: str | None = None
    message: str


class GarminConnectDisconnectResponse(BaseModel):
    """Response from Garmin Connect disconnect."""

    success: bool
    message: str
