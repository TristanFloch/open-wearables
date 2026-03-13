"""Garmin Connect workouts via Garth (PULL-based)."""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from garth import Client as GarthClient
from garth.data import Activity

from app.constants.workout_types.garmin import get_unified_workout_type
from app.database import DbSession
from app.schemas import EventRecordCreate, EventRecordDetailCreate
from app.services.event_record_service import event_record_service
from app.services.providers.garmin_connect.auth import GarminConnectAuth
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)


class GarminConnectWorkouts:
    """Fetch and normalize workouts from Garmin Connect via Garth."""

    def __init__(self, auth: GarminConnectAuth) -> None:
        self.auth = auth
        self.logger = logging.getLogger(self.__class__.__name__)

    def load_data(
        self,
        db: DbSession,
        user_id: UUID,
        **kwargs: Any,
    ) -> bool:
        """Fetch activities from Garmin Connect and save to database."""
        try:
            client = self.auth.get_client(db, user_id)
        except ValueError as e:
            log_structured(
                self.logger, "warning",
                f"Cannot fetch workouts for user {user_id}: {e}",
                provider="garmin_connect", task="load_data",
            )
            return False

        try:
            start_date = kwargs.get("start_date")
            end_date = kwargs.get("end_date")

            start_dt = self._parse_date(start_date, default=datetime.now() - timedelta(days=7))
            end_dt = self._parse_date(end_date, default=datetime.now())

            activities = self._fetch_activities(client, start_dt, end_dt)

            created_count = 0
            for activity in activities:
                try:
                    record, detail = self._normalize_activity(activity, user_id)
                    created_record = event_record_service.create(db, record)
                    detail_for_record = detail.model_copy(update={"record_id": created_record.id})
                    event_record_service.create_detail(db, detail_for_record)
                    created_count += 1
                except Exception as e:
                    log_structured(
                        self.logger, "warning",
                        f"Error saving activity: {e}",
                        provider="garmin_connect", task="load_data",
                    )

            self.auth.persist_refreshed_session(db, user_id, client)

            log_structured(
                self.logger, "info",
                f"Saved {created_count} activities for user {user_id}",
                provider="garmin_connect", task="load_data",
            )
            return True

        except Exception as e:
            log_structured(
                self.logger, "error",
                f"Error fetching activities: {e}",
                provider="garmin_connect", task="load_data",
            )
            self._handle_auth_error(db, user_id, e)
            return False

    def _fetch_activities(
        self,
        client: GarthClient,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[Activity]:
        """Fetch activities from Garth, paginating through results."""
        all_activities: list[Activity] = []
        offset = 0
        limit = 50

        while True:
            batch = Activity.list(limit=limit, start=offset, client=client)
            if not batch:
                break

            for activity in batch:
                # Activities are returned newest-first
                # Use start_time_gmt for comparison
                act_time = activity.start_time_gmt or activity.start_time_local
                if not act_time:
                    continue
                if act_time < start_dt:
                    return all_activities
                if act_time <= end_dt:
                    all_activities.append(activity)

            if len(batch) < limit:
                break
            offset += limit

        return all_activities

    def _normalize_activity(
        self,
        activity: Activity,
        user_id: UUID,
    ) -> tuple[EventRecordCreate, EventRecordDetailCreate]:
        """Map Garth Activity to EventRecordCreate + EventRecordDetailCreate."""
        workout_id = uuid4()

        # activity.activity_type is an ActivityType with type_key field
        type_key = activity.activity_type.type_key if activity.activity_type else "OTHER"
        workout_type = get_unified_workout_type(type_key)

        start_time = activity.start_time_gmt or activity.start_time_local
        # duration is in seconds (float)
        duration_seconds = int(activity.duration) if activity.duration else 0
        end_time = start_time + timedelta(seconds=duration_seconds) if start_time else None

        record = EventRecordCreate(
            id=workout_id,
            category="workout",
            type=workout_type.value,
            source_name="Garmin Connect",
            device_model=None,
            duration_seconds=duration_seconds,
            start_datetime=start_time,
            end_datetime=end_time or start_time,
            external_id=str(activity.activity_id),
            source="garmin_connect",
            user_id=user_id,
        )

        detail = EventRecordDetailCreate(
            record_id=workout_id,
            heart_rate_avg=Decimal(str(activity.average_hr)) if activity.average_hr else None,
            heart_rate_max=int(activity.max_hr) if activity.max_hr else None,
            heart_rate_min=int(activity.average_hr) if activity.average_hr else None,
            distance=Decimal(str(activity.distance)) if activity.distance else None,
            energy_burned=Decimal(str(activity.calories)) if activity.calories else None,
            steps_count=activity.steps,
            total_elevation_gain=Decimal(str(activity.elevation_gain)) if activity.elevation_gain else None,
            average_speed=Decimal(str(activity.average_speed)) if activity.average_speed else None,
            max_speed=Decimal(str(activity.max_speed)) if activity.max_speed else None,
        )

        return record, detail

    def _parse_date(self, value: str | None, default: datetime) -> datetime:
        if not value:
            return default
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return default

    def _handle_auth_error(self, db: DbSession, user_id: UUID, error: Exception) -> None:
        """Mark connection expired if the error is authentication-related."""
        error_str = str(error).lower()
        if "unauthorized" in error_str or "401" in error_str or "forbidden" in error_str:
            self.auth.mark_expired(db, user_id)
            log_structured(
                self.logger, "warning",
                f"Garmin Connect session expired for user {user_id}, marked as EXPIRED",
                provider="garmin_connect", task="auth_error",
            )
