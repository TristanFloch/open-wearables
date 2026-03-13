"""API routes for Garmin Connect authentication via Garth."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.database import DbSession
from app.integrations.celery.tasks import sync_vendor_data
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.garmin_connect import (
    GarminConnectDisconnectResponse,
    GarminConnectLoginRequest,
    GarminConnectLoginResponse,
    GarminConnectMFARequest,
)
from app.schemas.oauth import ConnectionStatus, UserConnectionUpdate
from app.services.providers.garmin_connect.auth import GarminConnectAuth
from garth.exc import GarthException, GarthHTTPError

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_auth() -> GarminConnectAuth:
    return GarminConnectAuth(connection_repo=UserConnectionRepository())


@router.post("/auth/login", status_code=status.HTTP_200_OK)
async def garmin_connect_login(
    request: GarminConnectLoginRequest,
    db: DbSession,
) -> GarminConnectLoginResponse:
    """Authenticate with Garmin Connect using personal credentials.

    If MFA is required, returns requires_mfa=True with a session_id
    to use with the /auth/mfa endpoint.
    """
    auth = _get_auth()

    # Check for existing active garmin or garmin_connect connection
    existing_garmin = auth.connection_repo.get_by_user_and_provider(db, request.user_id, "garmin")
    if existing_garmin and existing_garmin.status == ConnectionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has an active Garmin enterprise connection. "
            "Disconnect it before connecting via Garmin Connect.",
        )

    try:
        needs_mfa, session_id, client = auth.login(
            email=request.email,
            password=request.password.get_secret_value(),
        )
    except GarthHTTPError as e:
        logger.warning("Garmin Connect login HTTP error for user %s: %s", request.user_id, e)
        if hasattr(e, "error") and hasattr(e.error, "response") and e.error.response is not None:
            if e.error.response.status_code == 429:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Garmin SSO rate limit reached. Please wait a few minutes and try again.",
                )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Authentication failed. Check your email and password.",
        )
    except GarthException as e:
        logger.warning("Garmin Connect login failed for user %s: %s", request.user_id, e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Authentication failed. Check your email and password.",
        )
    except Exception as e:
        logger.error("Unexpected error during Garmin Connect login for user %s: %s", request.user_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="An unexpected error occurred connecting to Garmin. Please try again.",
        )

    if needs_mfa:
        return GarminConnectLoginResponse(
            success=False,
            requires_mfa=True,
            session_id=session_id,
            message="MFA code required. Submit it to /auth/mfa.",
        )

    # Login succeeded without MFA
    auth.save_session(db, request.user_id, client)

    # Trigger initial backfill sync
    sync_vendor_data.delay(
        user_id=str(request.user_id),
        providers=["garmin_connect"],
    )

    return GarminConnectLoginResponse(
        success=True,
        requires_mfa=False,
        message="Successfully connected to Garmin Connect.",
    )


@router.post("/auth/mfa", status_code=status.HTTP_200_OK)
async def garmin_connect_mfa(
    request: GarminConnectMFARequest,
    db: DbSession,
) -> GarminConnectLoginResponse:
    """Complete MFA for Garmin Connect login.

    Uses the session_id from the login response and the MFA code
    from the user's authenticator app or SMS.
    """
    auth = _get_auth()

    try:
        client = auth.complete_mfa(request.session_id, request.mfa_code)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except GarthHTTPError as e:
        logger.warning("MFA completion HTTP error: %s", e)
        if hasattr(e, "error") and hasattr(e.error, "response") and e.error.response is not None:
            if e.error.response.status_code == 429:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Garmin SSO rate limit reached. Please wait a few minutes and try again.",
                )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="MFA verification failed. Check your code and try again.",
        )
    except GarthException as e:
        logger.warning("MFA completion failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="MFA verification failed. Check your code and try again.",
        )
    except Exception as e:
        logger.error("Unexpected error during MFA completion: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="An unexpected error occurred connecting to Garmin. Please try again.",
        )

    auth.save_session(db, request.user_id, client)

    # Trigger initial backfill sync
    sync_vendor_data.delay(
        user_id=str(request.user_id),
        providers=["garmin_connect"],
    )

    return GarminConnectLoginResponse(
        success=True,
        requires_mfa=False,
        message="Successfully connected to Garmin Connect.",
    )


@router.delete("/auth/{user_id}", status_code=status.HTTP_200_OK)
async def garmin_connect_disconnect(
    user_id: UUID,
    db: DbSession,
) -> GarminConnectDisconnectResponse:
    """Disconnect Garmin Connect for a user."""
    auth = _get_auth()
    connection = auth.connection_repo.get_by_user_and_provider(db, user_id, "garmin_connect")

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Garmin Connect connection found for this user.",
        )

    auth.connection_repo.update(db, connection, UserConnectionUpdate(status=ConnectionStatus.REVOKED))

    return GarminConnectDisconnectResponse(
        success=True,
        message="Garmin Connect disconnected successfully.",
    )
