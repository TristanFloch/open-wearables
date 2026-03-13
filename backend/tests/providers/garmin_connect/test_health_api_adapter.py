"""Tests for Garth → Health API JSON transformation functions."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.services.providers.garmin_connect.health_api_adapter import (
    daily_summary_to_health_api,
    hrv_to_health_api,
    scores_to_health_api,
    sleep_to_health_api,
    stress_to_health_api,
    weight_to_health_api,
)


# ---------------------------------------------------------------------------
# Helpers to build mock Garth data objects
# ---------------------------------------------------------------------------


def _make_sleep_data(
    start_gmt_ms: int = 1705276800000,  # 2024-01-15 00:00:00 UTC
    end_gmt_ms: int = 1705305600000,    # 2024-01-15 08:00:00 UTC
    deep_sec: int = 3600,
    light_sec: int = 10800,
    rem_sec: int = 5400,
    awake_sec: int = 1800,
    sleep_score_value: int | None = 82,
    avg_respiration: float | None = 15.2,
    avg_spo2: float | None = 96.5,
    avg_hr_sleep: float | None = 58.0,
    sleep_id: int = 12345,
) -> MagicMock:
    dto = MagicMock()
    dto.sleep_start_timestamp_gmt = start_gmt_ms
    dto.sleep_end_timestamp_gmt = end_gmt_ms
    dto.deep_sleep_seconds = deep_sec
    dto.light_sleep_seconds = light_sec
    dto.rem_sleep_seconds = rem_sec
    dto.awake_sleep_seconds = awake_sec
    dto.average_sp_o2_hr_sleep = avg_hr_sleep
    dto.average_respiration_value = avg_respiration
    dto.average_sp_o2_value = avg_spo2
    dto.device_rem_capable = True
    dto.id = sleep_id

    if sleep_score_value is not None:
        overall = MagicMock()
        overall.value = sleep_score_value
        scores = MagicMock()
        scores.overall = overall
        dto.sleep_scores = scores
    else:
        dto.sleep_scores = None

    sleep = MagicMock()
    sleep.daily_sleep_dto = dto
    return sleep


def _make_daily_summary(**overrides) -> MagicMock:
    defaults = {
        "calendar_date": date(2024, 1, 15),
        "total_steps": 8500,
        "total_distance_meters": 6200,
        "active_kilocalories": 350,
        "total_kilocalories": 2100,
        "floors_ascended": 12.0,
        "resting_heart_rate": 55,
        "min_heart_rate": 45,
        "max_heart_rate": 165,
        "average_stress_level": 32,
        "max_stress_level": 78,
        "moderate_intensity_minutes": 30,
        "vigorous_intensity_minutes": 15,
        "body_battery_highest_value": 95,
        "body_battery_lowest_value": 20,
        "active_seconds": 3600,
        "sedentary_seconds": 28800,
    }
    defaults.update(overrides)
    summary = MagicMock()
    for k, v in defaults.items():
        setattr(summary, k, v)
    return summary


def _make_daily_heart_rate(
    calendar_date: date = date(2024, 1, 15),
) -> MagicMock:
    hr = MagicMock()
    hr.calendar_date = calendar_date
    # Midnight UTC for 2024-01-15 = 1705276800 seconds = 1705276800000 ms
    hr.heart_rate_values = [
        [1705276800000, 58],   # offset 0
        [1705280400000, 62],   # offset 3600
        [None, None],          # skip
        [1705284000000, None], # skip (no value)
    ]
    return hr


def _make_hrv_data() -> MagicMock:
    hrv = MagicMock()
    hrv.start_timestamp_gmt = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)

    summary = MagicMock()
    summary.calendar_date = date(2024, 1, 15)
    summary.last_night_avg = 42
    hrv.hrv_summary = summary

    reading1 = MagicMock()
    reading1.reading_time_gmt = datetime(2024, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
    reading1.hrv_value = 38
    reading2 = MagicMock()
    reading2.reading_time_gmt = datetime(2024, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
    reading2.hrv_value = 45
    hrv.hrv_readings = [reading1, reading2]

    return hrv


def _make_daily_body_battery_stress() -> MagicMock:
    dbs = MagicMock()
    dbs.start_timestamp_gmt = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    # stress_values_array: [[timestamp_ms, stress_value], ...]
    dbs.stress_values_array = [
        [1705276800000, 25],  # offset 0
        [1705277700000, 30],  # offset 900
        [1705278600000, None],  # skip
    ]
    # body_battery_values_array: [[timestamp_ms, status, level, version], ...]
    dbs.body_battery_values_array = [
        [1705276800000, "MEASURED", 80, 1.0],   # offset 0
        [1705277700000, "MEASURED", 78, 1.0],   # offset 900
        [1705278600000, None, None, None],       # skip
    ]
    return dbs


def _make_weight_data() -> MagicMock:
    w = MagicMock()
    w.timestamp_gmt = 1705320000000  # 2024-01-15 12:00:00 UTC in ms
    w.weight = 75000  # grams
    w.body_fat = 18.5
    w.bmi = 23.1
    w.sample_pk = 99887766
    return w


def _make_scores_data() -> MagicMock:
    s = MagicMock()
    s.calendar_date = date(2024, 1, 15)
    s.vo_2_max = 48.5
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSleepToHealthApi:
    def test_basic_mapping(self):
        sleep = _make_sleep_data()
        result = sleep_to_health_api(sleep)

        assert result["startTimeInSeconds"] == 1705276800
        assert result["durationInSeconds"] == 28800  # 8 hours
        assert result["deepSleepDurationInSeconds"] == 3600
        assert result["lightSleepDurationInSeconds"] == 10800
        assert result["remSleepInSeconds"] == 5400
        assert result["awakeDurationInSeconds"] == 1800
        assert result["averageHeartRate"] == 58.0
        assert result["respirationAvg"] == 15.2
        assert result["avgOxygenSaturation"] == 96.5
        assert result["overallSleepScore"] == {"value": 82}
        assert result["summaryId"] == "12345"
        assert result["validation"] == "ENHANCED_TENTATIVE"

    def test_none_sleep_score(self):
        sleep = _make_sleep_data(sleep_score_value=None)
        result = sleep_to_health_api(sleep)
        assert result["overallSleepScore"] is None

    def test_none_optional_fields(self):
        sleep = _make_sleep_data(
            avg_respiration=None, avg_spo2=None, avg_hr_sleep=None,
        )
        result = sleep_to_health_api(sleep)
        assert result["averageHeartRate"] is None
        assert result["respirationAvg"] is None
        assert result["avgOxygenSaturation"] is None


class TestDailySummaryToHealthApi:
    def test_basic_mapping(self):
        summary = _make_daily_summary()
        result = daily_summary_to_health_api(summary)

        assert result["calendarDate"] == "2024-01-15"
        assert result["steps"] == 8500
        assert result["distanceInMeters"] == 6200
        assert result["activeKilocalories"] == 350
        assert result["bmrKilocalories"] == 1750  # 2100 - 350
        assert result["floorsClimbed"] == 12
        assert result["restingHeartRateInBeatsPerMinute"] == 55
        assert result["averageStressLevel"] == 32
        assert result["moderateIntensityDurationInSeconds"] == 1800
        assert result["vigorousIntensityDurationInSeconds"] == 900
        assert result["activeSeconds"] == 3600
        assert result["sedentarySeconds"] == 28800

    def test_with_heart_rate_data(self):
        summary = _make_daily_summary()
        hr = _make_daily_heart_rate()
        result = daily_summary_to_health_api(summary, hr)

        hr_samples = result["timeOffsetHeartRateSamples"]
        assert hr_samples is not None
        assert hr_samples["0"] == 58
        assert hr_samples["3600"] == 62
        # None entries should be filtered out
        assert len(hr_samples) == 2

    def test_without_heart_rate_data(self):
        summary = _make_daily_summary()
        result = daily_summary_to_health_api(summary, None)
        assert result["timeOffsetHeartRateSamples"] is None

    def test_none_calories(self):
        summary = _make_daily_summary(
            total_kilocalories=None, active_kilocalories=None,
        )
        result = daily_summary_to_health_api(summary)
        assert result["bmrKilocalories"] is None

    def test_none_floors(self):
        summary = _make_daily_summary(floors_ascended=None)
        result = daily_summary_to_health_api(summary)
        assert result["floorsClimbed"] is None


class TestHrvToHealthApi:
    def test_basic_mapping(self):
        hrv = _make_hrv_data()
        result = hrv_to_health_api(hrv)

        assert result["startTimeInSeconds"] == 1705276800
        assert result["calendarDate"] == "2024-01-15"
        assert result["lastNightAvg"] == 42
        assert result["hrvValues"] is not None
        # reading1 at 01:00 = offset 3600, reading2 at 03:00 = offset 10800
        assert result["hrvValues"]["3600"] == 38
        assert result["hrvValues"]["10800"] == 45

    def test_no_readings(self):
        hrv = _make_hrv_data()
        hrv.hrv_readings = []
        result = hrv_to_health_api(hrv)
        assert result["hrvValues"] is None


class TestStressToHealthApi:
    def test_basic_mapping(self):
        dbs = _make_daily_body_battery_stress()
        result = stress_to_health_api(dbs)

        assert result["startTimeInSeconds"] == 1705276800
        stress_vals = result["stressLevelValues"]
        assert stress_vals is not None
        assert stress_vals["0"] == 25
        assert stress_vals["900"] == 30
        # None value should be filtered
        assert len(stress_vals) == 2

        bb_vals = result["bodyBatteryValues"]
        assert bb_vals is not None
        assert bb_vals["0"] == 80
        assert bb_vals["900"] == 78
        assert len(bb_vals) == 2

    def test_empty_arrays(self):
        dbs = _make_daily_body_battery_stress()
        dbs.stress_values_array = []
        dbs.body_battery_values_array = []
        result = stress_to_health_api(dbs)
        assert result["stressLevelValues"] is None
        assert result["bodyBatteryValues"] is None


class TestWeightToHealthApi:
    def test_basic_mapping(self):
        w = _make_weight_data()
        result = weight_to_health_api(w)

        assert result["measurementTimeInSeconds"] == 1705320000
        assert result["weightInGrams"] == 75000
        assert result["bodyFatInPercent"] == 18.5
        assert result["bodyMassIndex"] == 23.1
        assert result["summaryId"] == "99887766"

    def test_none_optional_fields(self):
        w = _make_weight_data()
        w.body_fat = None
        w.bmi = None
        result = weight_to_health_api(w)
        assert result["bodyFatInPercent"] is None
        assert result["bodyMassIndex"] is None


class TestScoresToHealthApi:
    def test_basic_mapping(self):
        s = _make_scores_data()
        result = scores_to_health_api(s)

        assert result["calendarDate"] == "2024-01-15"
        assert result["vo2Max"] == 48.5
        assert result["summaryId"] is None
