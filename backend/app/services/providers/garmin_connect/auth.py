"""Garmin Connect authentication via Garth (personal account credentials).

Garth manages two token layers:
- OAuth1 token: ~1 year lifetime, obtained at login, no auto-refresh.
- OAuth2 token: short-lived, auto-refreshed transparently by Garth.

We serialize the full Garth session (both tokens) and store it encrypted
in UserConnection.access_token. After every sync cycle we re-serialize
to capture any OAuth2 refresh that happened during API calls.
"""

import base64
import logging
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from garth import Client as GarthClient
from garth import sso

from app.config import settings
from app.database import DbSession
from app.integrations.redis_client import get_redis_client
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas import ConnectionStatus, UserConnectionCreate, UserConnectionUpdate

logger = logging.getLogger(__name__)

OAUTH1_LIFETIME_DAYS = 365
MFA_REDIS_TTL_SECONDS = 300
MFA_REDIS_PREFIX = "garmin_connect:mfa:"


def _encrypt_session(session_data: str) -> str:
    """Encrypt serialized Garth session with Fernet."""
    return settings.fernet_decryptor.encrypt(session_data.encode("utf-8")).decode("utf-8")


def _decrypt_session(encrypted: str) -> str:
    """Decrypt serialized Garth session with Fernet."""
    return settings.fernet_decryptor.decrypt(encrypted.encode("utf-8")).decode("utf-8")


class GarminConnectAuth:
    """Handles Garmin Connect authentication via Garth library."""

    def __init__(self, connection_repo: UserConnectionRepository) -> None:
        self.connection_repo = connection_repo

    def login(
        self,
        email: str,
        password: str,
    ) -> tuple[bool, str | None, GarthClient | None]:
        """Attempt login with Garmin Connect credentials.

        Uses garth.sso.login() directly (not Client.login()) to support
        the return_on_mfa flow.

        Returns:
            Tuple of (needs_mfa, session_id_or_none, client_or_none).
            If needs_mfa is True, session_id is set and client is None.
            If needs_mfa is False, session_id is None and client is set.
        """
        client = GarthClient()
        result = sso.login(email, password, client=client, return_on_mfa=True)

        # sso.login returns ("needs_mfa", {client_state}) when MFA required
        if isinstance(result, tuple) and len(result) == 2 and result[0] == "needs_mfa":
            client_state: dict[str, Any] = result[1]
            session_id = str(uuid4())

            # Pickle the client_state (contains the HTTP client with session cookies)
            pickled = base64.b64encode(pickle.dumps(client_state)).decode("ascii")
            redis_client = get_redis_client()
            redis_client.setex(
                f"{MFA_REDIS_PREFIX}{session_id}",
                MFA_REDIS_TTL_SECONDS,
                pickled,
            )
            return True, session_id, None

        # Login succeeded — result is (OAuth1Token, OAuth2Token)
        # sso.login() returns tokens but doesn't set them on the client
        oauth1_token, oauth2_token = result
        client.configure(oauth1_token=oauth1_token, oauth2_token=oauth2_token)
        return False, None, client

    def complete_mfa(self, session_id: str, mfa_code: str) -> GarthClient:
        """Complete MFA login using the code and stored partial session.

        Raises:
            ValueError: If session_id is invalid/expired or MFA fails.
        """
        redis_client = get_redis_client()
        redis_key = f"{MFA_REDIS_PREFIX}{session_id}"
        pickled = redis_client.get(redis_key)

        if not pickled:
            raise ValueError("MFA session expired or invalid")

        client_state: dict[str, Any] = pickle.loads(base64.b64decode(pickled))  # noqa: S301

        # resume_login completes the MFA and returns (OAuth1Token, OAuth2Token)
        oauth1_token, oauth2_token = sso.resume_login(client_state, mfa_code)

        # Build a new GarthClient with the obtained tokens
        client = GarthClient()
        client.configure(oauth1_token=oauth1_token, oauth2_token=oauth2_token)

        # Clean up Redis
        redis_client.delete(redis_key)

        return client

    def save_session(
        self,
        db: DbSession,
        user_id: UUID,
        client: GarthClient,
    ) -> None:
        """Save or update Garth session in UserConnection."""
        encrypted_session = _encrypt_session(client.dumps())
        token_expires_at = datetime.now(timezone.utc) + timedelta(days=OAUTH1_LIFETIME_DAYS)

        connection = self.connection_repo.get_by_user_and_provider(
            db, user_id, "garmin_connect"
        )

        if connection:
            update = UserConnectionUpdate(
                access_token=encrypted_session,
                token_expires_at=token_expires_at,
                status=ConnectionStatus.ACTIVE,
            )
            self.connection_repo.update(db, connection, update)
        else:
            create = UserConnectionCreate(
                id=uuid4(),
                user_id=user_id,
                provider="garmin_connect",
                access_token=encrypted_session,
                token_expires_at=token_expires_at,
                status=ConnectionStatus.ACTIVE,
            )
            self.connection_repo.create(db, create)

    def get_client(self, db: DbSession, user_id: UUID) -> GarthClient:
        """Restore a Garth client from stored session.

        Raises:
            ValueError: If no connection exists or session is expired.
        """
        connection = self.connection_repo.get_by_user_and_provider(
            db, user_id, "garmin_connect"
        )

        if not connection or not connection.access_token:
            raise ValueError("No Garmin Connect session found for user")

        if connection.status != ConnectionStatus.ACTIVE.value:
            raise ValueError(f"Garmin Connect connection is {connection.status}")

        # Check OAuth1 token expiry
        if connection.token_expires_at and connection.token_expires_at < datetime.now(timezone.utc):
            self.connection_repo.update(
                db,
                connection,
                UserConnectionUpdate(status=ConnectionStatus.EXPIRED),
            )
            raise ValueError("Garmin Connect session expired — re-authentication required")

        decrypted_session = _decrypt_session(connection.access_token)
        client = GarthClient()
        client.loads(decrypted_session)
        return client

    def persist_refreshed_session(
        self,
        db: DbSession,
        user_id: UUID,
        client: GarthClient,
    ) -> None:
        """Re-serialize and save the Garth session after API calls.

        This captures any OAuth2 token refresh that Garth performed
        transparently during data fetching.
        """
        connection = self.connection_repo.get_by_user_and_provider(
            db, user_id, "garmin_connect"
        )
        if connection:
            encrypted_session = _encrypt_session(client.dumps())
            self.connection_repo.update(
                db,
                connection,
                UserConnectionUpdate(access_token=encrypted_session),
            )

    def mark_expired(self, db: DbSession, user_id: UUID) -> None:
        """Mark the connection as expired (OAuth1 token died)."""
        connection = self.connection_repo.get_by_user_and_provider(
            db, user_id, "garmin_connect"
        )
        if connection:
            self.connection_repo.update(
                db,
                connection,
                UserConnectionUpdate(status=ConnectionStatus.EXPIRED),
            )
