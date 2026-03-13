"""Garmin Connect 247 data adapter.

Subclasses Garmin247Data and overrides only the data-fetching methods to
pull from Garmin Connect via Garth, transforming responses into the Health
API JSON format so all normalization, build, and save logic is reused.
"""

import contextlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Never
from uuid import UUID, uuid4

from garth import Client as GarthClient
from garth.data import (
    DailyHeartRate,
    DailySummary,
    GarminScoresData,
    HRVData,
    SleepData,
    WeightData,
)
from garth.data.body_battery import DailyBodyBatteryStress
from garth.utils import date_range, format_end_date

from app.config import settings as app_settings
from app.database import DbSession
from app.schemas import EventRecordCreate, TimeSeriesSampleCreate
from app.schemas.event_record_detail import EventRecordDetailCreate
from app.schemas.series_types import SeriesType
from app.services.providers.garmin.data_247 import Garmin247Data
from app.services.providers.garmin_connect.auth import GarminConnectAuth
from app.services.providers.garmin_connect.health_api_adapter import (
    daily_summary_to_health_api,
    hrv_to_health_api,
    scores_to_health_api,
    sleep_to_health_api,
    stress_to_health_api,
    weight_to_health_api,
)
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)


class _NullOAuth:
    """Placeholder to satisfy Base247DataTemplate.__init__ signature.

    Never actually used — the adapter overrides all fetch methods.
    """


class GarminConnect247DataAdapter(Garmin247Data):
    """Garmin Connect 247 data via Garth, reusing Garmin247Data normalization.

    Overrides fetch methods to pull data from Garmin Connect and transform
    it to Health API format. All normalize_*, _build_*, save_*, and
    process_items_batch methods are inherited from Garmin247Data.
    """

    def __init__(self, auth: GarminConnectAuth) -> None:
        super().__init__(
            provider_name="garmin_connect",
            api_base_url="https://connect.garmin.com",
            oauth=_NullOAuth(),
        )
        self.auth = auth

    # ------------------------------------------------------------------
    # Guard: prevent accidental Health API calls
    # ------------------------------------------------------------------

    def _make_api_request(
        self,
        db: DbSession,
        user_id: UUID,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Never:
        raise NotImplementedError("GarminConnect247DataAdapter fetches via Garth, not Health API.")

    # ------------------------------------------------------------------
    # Override: source_name for records
    # ------------------------------------------------------------------

    def _build_sleep_record(
        self, user_id: UUID, normalized_sleep: dict[str, Any]
    ) -> tuple[EventRecordCreate, EventRecordDetailCreate] | None:
        result = super()._build_sleep_record(user_id, normalized_sleep)
        if result:
            record, detail = result
            record.source_name = "Garmin Connect"
            return record, detail
        return None

    def _build_activity_record(
        self, user_id: UUID, raw_activity: dict[str, Any]
    ) -> tuple[EventRecordCreate, EventRecordDetailCreate] | None:
        result = super()._build_activity_record(user_id, raw_activity)
        if result:
            record, detail = result
            record.source_name = "Garmin Connect"
            return record, detail
        return None

    def normalize_dailies(
        self,
        raw_daily: dict[str, Any],
        user_id: UUID,
    ) -> dict[str, Any]:
        """Extend parent normalization with Connect-specific fields."""
        normalized = super().normalize_dailies(raw_daily, user_id)
        normalized["active_seconds"] = raw_daily.get("activeSeconds")
        normalized["sedentary_seconds"] = raw_daily.get("sedentarySeconds")
        return normalized

    def _build_dailies_samples(
        self,
        user_id: UUID,
        normalized_daily: dict[str, Any],
    ) -> list[TimeSeriesSampleCreate]:
        """Extend parent with Connect-specific daily series (exercise/sedentary time)."""
        samples = super()._build_dailies_samples(user_id, normalized_daily)

        # Derive recorded_at the same way the parent does
        start_ts = normalized_daily.get("start_time_seconds")
        calendar_date = normalized_daily.get("calendar_date")
        if start_ts:
            recorded_at = self._from_epoch_seconds(start_ts)
        elif calendar_date:
            try:
                recorded_at = datetime.strptime(calendar_date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
            except ValueError:
                return samples
        else:
            return samples

        # Connect-specific: activeSeconds → exercise_time (minutes)
        active_sec = normalized_daily.get("active_seconds")
        if active_sec is not None:
            samples.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=recorded_at,
                    value=Decimal(str(active_sec // 60)),
                    series_type=SeriesType.exercise_time,
                )
            )

        # Connect-specific: sedentarySeconds → sedentary_time (minutes)
        sedentary_sec = normalized_daily.get("sedentary_seconds")
        if sedentary_sec is not None:
            samples.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=recorded_at,
                    value=Decimal(str(sedentary_sec // 60)),
                    series_type=SeriesType.sedentary_time,
                )
            )

        return samples

    # ------------------------------------------------------------------
    # Override: fetch methods (Garth → Health API format)
    # ------------------------------------------------------------------

    def get_sleep_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        client = self._get_client(db, user_id)
        days, end_str = self._garth_range(start_time, end_time)
        sleep_list = SleepData.list(end_str, days, client=client)
        return [d for s in sleep_list if (d := sleep_to_health_api(s)) is not None]

    def get_dailies_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        client = self._get_client(db, user_id)
        days, end_str = self._garth_range(start_time, end_time)

        summaries = DailySummary.list(end_str, days, client=client)
        hr_list = DailyHeartRate.list(end_str, days, client=client)

        # Index HR data by calendar_date for merging
        hr_by_date: dict[str, DailyHeartRate] = {}
        for hr in hr_list:
            hr_by_date[str(hr.calendar_date)] = hr

        return [daily_summary_to_health_api(s, hr_by_date.get(str(s.calendar_date))) for s in summaries]

    def get_epochs_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        # Epochs are not available via Garth data classes.
        # HR time-series is already captured via dailies (timeOffsetHeartRateSamples).
        return []

    def get_body_composition(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        client = self._get_client(db, user_id)
        days, end_str = self._garth_range(start_time, end_time)
        weight_list = WeightData.list(end_str, days, client=client)
        return [d for w in weight_list if (d := weight_to_health_api(w)) is not None]

    # ------------------------------------------------------------------
    # Garth-only data types (no Health API get_* method to override)
    # ------------------------------------------------------------------

    def _fetch_hrv_items(
        self,
        client: GarthClient,
        days: int,
        end_str: str,
    ) -> list[dict[str, Any]]:
        hrv_list = self._safe_garth_list(HRVData, end_str, days, client)
        return [hrv_to_health_api(h) for h in hrv_list]

    def _fetch_stress_items(
        self,
        client: GarthClient,
        days: int,
        end_str: str,
    ) -> list[dict[str, Any]]:
        dbs_list = self._safe_garth_list(DailyBodyBatteryStress, end_str, days, client)
        return [stress_to_health_api(dbs) for dbs in dbs_list]

    def _fetch_user_metrics_items(
        self,
        client: GarthClient,
        days: int,
        end_str: str,
    ) -> list[dict[str, Any]]:
        scores_list = self._safe_garth_list(GarminScoresData, end_str, days, client)
        return [scores_to_health_api(s) for s in scores_list]

    # ------------------------------------------------------------------
    # Override: main orchestrator
    # ------------------------------------------------------------------

    def load_and_save_all(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        is_first_sync: bool = False,
    ) -> dict[str, Any]:
        """Fetch all 247 data from Garmin Connect and save via parent pipeline."""
        try:
            client = self.auth.get_client(db, user_id)
        except ValueError as e:
            log_structured(
                self.logger,
                "warning",
                f"Cannot fetch 247 data for user {user_id}: {e}",
                provider="garmin_connect",
                task="load_and_save_all",
            )
            return {"sync_complete": False, "total_saved": 0, "error": str(e)}

        # Cache client for get_* methods called via process_items_batch
        self._current_client = client

        start_dt = self._parse_datetime(
            start_time,
            default=datetime.now(timezone.utc) - timedelta(days=7),
        )
        end_dt = self._parse_datetime(
            end_time,
            default=datetime.now(timezone.utc),
        )

        if is_first_sync:
            backfill_days = getattr(app_settings, "garmin_connect_backfill_days", 90)
            start_dt = datetime.now(timezone.utc) - timedelta(days=backfill_days)

        days, end_str = self._garth_range(start_dt, end_dt)

        results: dict[str, Any] = {}
        total_saved = 0

        # Types that go through get_* overrides → process_items_batch
        fetch_via_get = [
            ("sleeps", lambda: self.get_sleep_data(db, user_id, start_dt, end_dt)),
            ("dailies", lambda: self.get_dailies_data(db, user_id, start_dt, end_dt)),
            ("bodyComps", lambda: self.get_body_composition(db, user_id, start_dt, end_dt)),
        ]

        # Types that use Garth-only fetchers
        fetch_via_garth = [
            ("hrv", lambda: self._fetch_hrv_items(client, days, end_str)),
            ("stressDetails", lambda: self._fetch_stress_items(client, days, end_str)),
            ("userMetrics", lambda: self._fetch_user_metrics_items(client, days, end_str)),
        ]

        for summary_type, fetcher in [*fetch_via_get, *fetch_via_garth]:
            try:
                items = fetcher()
                if items:
                    count = self.process_items_batch(db, user_id, summary_type, items)
                    results[summary_type] = {"success": True, "count": count}
                    total_saved += count
                else:
                    results[summary_type] = {"success": True, "count": 0}
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    f"Error fetching {summary_type} data: {e}",
                    provider="garmin_connect",
                    task="load_and_save_all",
                )
                results[summary_type] = {"success": False, "error": str(e)}
                self._handle_auth_error(db, user_id, e)

        with contextlib.suppress(Exception):
            self.auth.persist_refreshed_session(db, user_id, client)

        self._current_client = None

        return {"sync_complete": True, "total_saved": total_saved, "details": results}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_garth_list(
        data_cls: type,
        end_str: str,
        days: int,
        client: GarthClient,
    ) -> list:
        """Call data_cls.list() with per-day error tolerance.

        Garth data classes use strict Pydantic validation. Some users lack
        fields that Garth marks as required (e.g. HRVData.baseline,
        GarminScoresData.hill_score). When .list() fails because a single
        day has invalid data, we fall back to fetching each day individually
        and skipping days that fail validation.
        """
        try:
            return data_cls.list(end_str, days, client=client)
        except Exception:
            pass

        # Fallback: fetch day-by-day, skip failures
        results = []
        end_date = format_end_date(end_str)
        for day in date_range(end_date, days):
            try:
                item = data_cls.get(day, client=client)
                if item is not None:
                    if isinstance(item, list):
                        results.extend(item)
                    else:
                        results.append(item)
            except Exception:
                continue
        return results

    def _get_client(self, db: DbSession, user_id: UUID) -> GarthClient:
        """Return cached client from load_and_save_all, or fetch a new one."""
        if hasattr(self, "_current_client") and self._current_client is not None:
            return self._current_client
        return self.auth.get_client(db, user_id)

    @staticmethod
    def _garth_range(
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[int, str]:
        """Compute days count and end date string for Garth .list() calls."""
        days = max((end_dt - start_dt).days, 1)
        end_str = end_dt.strftime("%Y-%m-%d")
        return days, end_str

    def _parse_datetime(
        self,
        value: datetime | str | None,
        default: datetime,
    ) -> datetime:
        if value is None:
            return default
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return default

    def _handle_auth_error(
        self,
        db: DbSession,
        user_id: UUID,
        error: Exception,
    ) -> None:
        error_str = str(error).lower()
        if "unauthorized" in error_str or "401" in error_str or "forbidden" in error_str:
            self.auth.mark_expired(db, user_id)
