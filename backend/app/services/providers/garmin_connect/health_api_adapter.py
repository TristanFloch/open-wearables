"""Transform Garth/Connect API data classes into Garmin Health API JSON format.

These pure functions allow the existing Garmin247Data normalize/build/save
pipeline to process data fetched from Garmin Connect via Garth, without
any changes to the normalization logic.

Health API reference keys used by Garmin247Data:
- Sleep: startTimeInSeconds, durationInSeconds, deepSleepDurationInSeconds, etc.
- Dailies: calendarDate, startTimeInSeconds, steps, distanceInMeters, etc.
- HRV: startTimeInSeconds, calendarDate, lastNightAvg, hrvValues
- Stress: startTimeInSeconds, stressLevelValues, bodyBatteryValues
- Body comp: measurementTimeInSeconds, weightInGrams, bodyFatInPercent, etc.
- User metrics: calendarDate, vo2Max, summaryId
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from garth.data import (
    DailyHeartRate,
    DailySummary,
    GarminScoresData,
    HRVData,
    SleepData,
    WeightData,
)
from garth.data.body_battery import DailyBodyBatteryStress


def _midnight_epoch(d: date) -> int:
    """Convert a date to epoch seconds at midnight UTC."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _ts_ms_to_offset_dict(
    base_ts_seconds: int,
    pairs: list[list[int | None]],
    value_index: int = 1,
) -> dict[str, int]:
    """Convert [timestamp_ms, value, ...] pairs to {offset_seconds_str: value}.

    This matches the Health API format used by timeOffsetHeartRateSamples,
    stressLevelValues, bodyBatteryValues, and hrvValues.
    """
    result: dict[str, int] = {}
    for pair in pairs:
        if len(pair) <= value_index:
            continue
        ts_ms = pair[0]
        value = pair[value_index]
        if ts_ms is None or value is None:
            continue
        offset = (ts_ms // 1000) - base_ts_seconds
        result[str(offset)] = value
    return result


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------


def sleep_to_health_api(sleep: SleepData) -> dict[str, Any] | None:
    """Map Garth SleepData → Health API /wellness-api/rest/sleeps dict."""
    dto = sleep.daily_sleep_dto

    if dto.sleep_start_timestamp_gmt is None or dto.sleep_end_timestamp_gmt is None:
        return None

    start_ts = dto.sleep_start_timestamp_gmt // 1000
    end_ts = dto.sleep_end_timestamp_gmt // 1000
    duration = end_ts - start_ts

    sleep_score: dict[str, Any] | None = None
    if dto.sleep_scores and dto.sleep_scores.overall:
        sleep_score = {"value": dto.sleep_scores.overall.value}

    return {
        "startTimeInSeconds": start_ts,
        "durationInSeconds": duration,
        "deepSleepDurationInSeconds": dto.deep_sleep_seconds,
        "lightSleepDurationInSeconds": dto.light_sleep_seconds,
        "remSleepInSeconds": dto.rem_sleep_seconds,
        "awakeDurationInSeconds": dto.awake_sleep_seconds,
        "averageHeartRate": dto.average_sp_o2_hr_sleep,
        "lowestHeartRate": None,
        "respirationAvg": dto.average_respiration_value,
        "avgOxygenSaturation": dto.average_sp_o2_value,
        "overallSleepScore": sleep_score,
        "validation": "ENHANCED_TENTATIVE" if dto.device_rem_capable else None,
        "summaryId": str(dto.id) if dto.id else None,
    }


# ---------------------------------------------------------------------------
# Dailies
# ---------------------------------------------------------------------------


def daily_summary_to_health_api(
    summary: DailySummary,
    heart_rate: DailyHeartRate | None = None,
) -> dict[str, Any]:
    """Map Garth DailySummary (+ optional DailyHeartRate) → Health API dailies dict."""
    cal_date = summary.calendar_date
    start_ts = _midnight_epoch(cal_date)

    bmr: int | None = None
    if summary.total_kilocalories is not None and summary.active_kilocalories is not None:
        bmr = summary.total_kilocalories - summary.active_kilocalories

    # Convert HR time-series to offset dict
    hr_offset_samples: dict[str, int] | None = None
    if heart_rate and heart_rate.heart_rate_values:
        hr_offset_samples = {}
        for pair in heart_rate.heart_rate_values:
            if len(pair) >= 2 and pair[0] is not None and pair[1] is not None:
                offset = (pair[0] // 1000) - start_ts
                hr_offset_samples[str(offset)] = pair[1]

    return {
        "calendarDate": str(cal_date),
        "startTimeInSeconds": start_ts,
        "steps": summary.total_steps,
        "distanceInMeters": summary.total_distance_meters,
        "activeKilocalories": summary.active_kilocalories,
        "bmrKilocalories": bmr,
        "floorsClimbed": int(summary.floors_ascended) if summary.floors_ascended is not None else None,
        "minHeartRateInBeatsPerMinute": summary.min_heart_rate,
        "maxHeartRateInBeatsPerMinute": summary.max_heart_rate,
        "averageHeartRateInBeatsPerMinute": None,
        "restingHeartRateInBeatsPerMinute": summary.resting_heart_rate,
        "averageStressLevel": summary.average_stress_level,
        "maxStressLevel": summary.max_stress_level,
        "moderateIntensityDurationInSeconds": ((summary.moderate_intensity_minutes or 0) * 60),
        "vigorousIntensityDurationInSeconds": ((summary.vigorous_intensity_minutes or 0) * 60),
        "bodyBatteryHighestValue": summary.body_battery_highest_value,
        "bodyBatteryLowestValue": summary.body_battery_lowest_value,
        "timeOffsetHeartRateSamples": hr_offset_samples,
        "summaryId": None,
        # Connect-specific fields (not in Health API dailies)
        "activeSeconds": summary.active_seconds,
        "sedentarySeconds": summary.sedentary_seconds,
    }


# ---------------------------------------------------------------------------
# HRV
# ---------------------------------------------------------------------------


def hrv_to_health_api(hrv: HRVData) -> dict[str, Any]:
    """Map Garth HRVData → Health API /wellness-api/rest/hrv dict."""
    start_ts = int(hrv.start_timestamp_gmt.timestamp())
    cal_date = str(hrv.hrv_summary.calendar_date)

    # Build hrvValues offset dict from individual readings
    hrv_values: dict[str, int] = {}
    if hrv.hrv_readings:
        for reading in hrv.hrv_readings:
            offset = int(reading.reading_time_gmt.timestamp()) - start_ts
            hrv_values[str(offset)] = reading.hrv_value

    return {
        "startTimeInSeconds": start_ts,
        "calendarDate": cal_date,
        "lastNightAvg": hrv.hrv_summary.last_night_avg,
        "hrvValues": hrv_values if hrv_values else None,
        "summaryId": None,
    }


# ---------------------------------------------------------------------------
# Stress + Body Battery
# ---------------------------------------------------------------------------


def stress_to_health_api(dbs: DailyBodyBatteryStress) -> dict[str, Any]:
    """Map Garth DailyBodyBatteryStress → Health API stressDetails dict.

    DailyBodyBatteryStress contains both stress_values_array and
    body_battery_values_array, which maps to the Health API stressDetails
    format that includes both stressLevelValues and bodyBatteryValues.
    """
    start_ts = int(dbs.start_timestamp_gmt.timestamp())

    # stress_values_array: [[timestamp_ms, stress_value], ...]
    stress_offset: dict[str, int] = {}
    if dbs.stress_values_array:
        for pair in dbs.stress_values_array:
            if len(pair) >= 2 and pair[0] is not None and pair[1] is not None:
                offset = (pair[0] // 1000) - start_ts
                stress_offset[str(offset)] = pair[1]

    # body_battery_values_array: [[timestamp_ms, status, level, version], ...]
    bb_offset: dict[str, int] = {}
    if dbs.body_battery_values_array:
        for values in dbs.body_battery_values_array:
            if len(values) >= 3 and values[0] is not None and values[2] is not None:
                offset = (values[0] // 1000) - start_ts
                bb_offset[str(offset)] = values[2]

    return {
        "startTimeInSeconds": start_ts,
        "stressLevelValues": stress_offset if stress_offset else None,
        "bodyBatteryValues": bb_offset if bb_offset else None,
    }


# ---------------------------------------------------------------------------
# Body Composition / Weight
# ---------------------------------------------------------------------------


def weight_to_health_api(w: WeightData) -> dict[str, Any] | None:
    """Map Garth WeightData → Health API /wellness-api/rest/bodyComps dict."""
    if w.timestamp_gmt is None:
        return None

    return {
        "measurementTimeInSeconds": w.timestamp_gmt // 1000,
        "weightInGrams": w.weight,
        "bodyFatInPercent": w.body_fat,
        "bodyMassIndex": w.bmi,
        "summaryId": str(w.sample_pk) if w.sample_pk else None,
    }


# ---------------------------------------------------------------------------
# User Metrics (VO2max, fitness scores)
# ---------------------------------------------------------------------------


def scores_to_health_api(scores: GarminScoresData) -> dict[str, Any]:
    """Map Garth GarminScoresData → Health API /wellness-api/rest/userMetrics dict."""
    return {
        "calendarDate": str(scores.calendar_date),
        "vo2Max": scores.vo_2_max,
        "fitnessAge": None,
        "summaryId": None,
    }
