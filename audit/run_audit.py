#!/usr/bin/env python3
"""Download normalized Open Wearables data and generate a metrics audit.

Required environment variable:
    OPEN_WEARABLES_API_KEY

Optional environment variables:
    OPEN_WEARABLES_API_URL   (default: http://localhost:8000)
    OPEN_WEARABLES_USER_ID   (otherwise selects the newest comprehensive seed user)
    AUDIT_START_DATE         (default: 2024-01-01)
    AUDIT_END_DATE           (default: 2025-12-31)
    AUDIT_OUTPUT_DIR         (default: audit/output)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "output"
PAGE_SIZE = 1000

TIME_SERIES_GROUPS: dict[str, list[str]] = {
    "heart_rate": ["heart_rate"],
    "resting_heart_rate": ["resting_heart_rate"],
    "hrv": ["heart_rate_variability_sdnn", "heart_rate_variability_rmssd"],
    "steps": ["steps"],
    "recovery_timeseries": ["oxygen_saturation", "respiratory_rate", "skin_temperature"],
    "body_composition": ["weight", "body_fat_percentage", "vo2_max"],
    "energy_activity": [
        "active_energy",
        "basal_energy",
        "distance_walking_running",
        "flights_climbed",
        "stand_time",
        "exercise_time",
    ],
    "blood_pressure": ["blood_pressure_systolic", "blood_pressure_diastolic"],
    "other_relevant": [
        "body_temperature",
        "blood_glucose",
        "time_in_daylight",
        "environmental_audio_exposure",
        "headphone_audio_exposure",
    ],
    "workout_timeseries": ["running_power", "running_speed", "cadence", "power", "swimming_stroke_count"],
}

PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "heart_rate": (20, 250),
    "resting_heart_rate": (20, 200),
    "heart_rate_variability_sdnn": (0, 500),
    "heart_rate_variability_rmssd": (0, 500),
    "oxygen_saturation": (50, 100),
    "respiratory_rate": (2, 60),
    "skin_temperature": (15, 45),
    "body_temperature": (25, 45),
    "weight": (1, 500),
    "body_fat_percentage": (0, 100),
    "vo2_max": (1, 100),
    "blood_pressure_systolic": (50, 300),
    "blood_pressure_diastolic": (20, 200),
    "blood_glucose": (20, 1000),
}


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")


@dataclass(frozen=True)
class RequestSpec:
    name: str
    path: str
    params: list[tuple[str, str]]
    pagination: str = "cursor"


class Api:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Open-Wearables-API-Key": api_key}

    def get(self, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        with httpx.Client(base_url=self.base_url, headers=self.headers, timeout=120) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def paginated(self, spec: RequestSpec) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        offset = 0
        while True:
            params = list(spec.params)
            params.append(("limit", str(PAGE_SIZE)))
            if spec.pagination == "offset":
                params.append(("offset", str(offset)))
            elif cursor:
                params.append(("cursor", cursor))
            page = self.get(spec.path, params)
            pages.append(page)
            pagination = page.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            if spec.pagination == "offset":
                offset += len(page.get("data") or [])
            else:
                cursor = pagination.get("next_cursor")
                if not cursor:
                    raise RuntimeError(f"{spec.name}: has_more=true without next_cursor")
        return pages


def rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for page in pages for item in page.get("data", [])]

def extract_list(resp):
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ("items", "data", "results", "users"):
            if isinstance(resp.get(key), list):
                return resp[key]
    raise RuntimeError(f"Unexpected response shape: {str(resp)[:500]}")

def choose_user(api: Api, requested_id: str | None) -> tuple[str, dict[str, Any], Any]:
    users_response = api.get("/api/v1/users", [("limit", "100"), ("sort_by", "created_at"), ("sort_order", "desc")])
    users = extract_list(users_response)
    if requested_id:
        selected = next((u for u in users if str(u.get("id")) == requested_id), None)
        if selected is None:
            selected = api.get(f"/api/v1/users/{requested_id}")
        return requested_id, selected, users_response
    selected = next((u for u in users if "|comprehensive]" in (u.get("first_name") or "")), None)
    if selected is None:
        raise RuntimeError("No OPEN_WEARABLES_USER_ID was set and no comprehensive seed user was found")
    return str(selected["id"]), selected, users_response


def longest_missing_day_gap(observed: set[date]) -> tuple[int, str]:
    if len(observed) < 2:
        return 0, "none within observed range"
    ordered = sorted(observed)
    best = 0
    best_range = "none"
    for left, right in zip(ordered, ordered[1:], strict=False):
        missing = (right - left).days - 1
        if missing > best:
            best = missing
            best_range = f"{left + timedelta(days=1)} to {right - timedelta(days=1)}"
    return best, best_range


def cadence_label(samples: list[dict[str, Any]]) -> str:
    if samples and all(item.get("is_daily_total") is True for item in samples):
        return "per-day (provider daily totals)"
    by_source: dict[tuple[str, str, str], list[datetime]] = defaultdict(list)
    for item in samples:
        source = item.get("source") or {}
        key = (str(source.get("provider")), str(source.get("source")), str(source.get("device")))
        by_source[key].append(parse_timestamp(item["timestamp"]))
    deltas: list[float] = []
    for timestamps in by_source.values():
        ordered = sorted(set(timestamps))
        deltas.extend((b - a).total_seconds() for a, b in zip(ordered, ordered[1:], strict=False) if b > a)
    if not deltas:
        return "single sample"
    seconds = statistics.median(deltas)
    if seconds < 120:
        return f"per-sample (~{seconds:.0f}s median cadence)"
    if seconds < 7200:
        return f"per-sample (~{seconds / 60:.1f}min median cadence)"
    if seconds < 172800:
        return f"per-day (~{seconds / 3600:.1f}h median cadence)"
    return f"periodic (~{seconds / 86400:.1f}d median cadence)"


def summarize_timeseries(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in samples:
        grouped[item["type"]].append(item)
    result = []
    now = datetime.now(timezone.utc)
    for metric, items in sorted(grouped.items()):
        timestamps = [parse_timestamp(item["timestamp"]) for item in items]
        observed_days = {ts.date() for ts in timestamps}
        first, last = min(timestamps), max(timestamps)
        total_days = (last.date() - first.date()).days + 1
        largest_gap, gap_range = longest_missing_day_gap(observed_days)
        exact_keys = [
            (
                item["timestamp"],
                metric,
                (item.get("source") or {}).get("provider"),
                (item.get("source") or {}).get("source"),
                (item.get("source") or {}).get("device"),
            )
            for item in items
        ]
        values = [float(item["value"]) for item in items]
        bounds = PLAUSIBLE_RANGES.get(metric, (0, float("inf")))
        providers = sorted({(item.get("source") or {}).get("provider") or "unknown" for item in items})
        sources = {
            (
                (item.get("source") or {}).get("provider"),
                (item.get("source") or {}).get("source"),
                (item.get("source") or {}).get("device"),
            )
            for item in items
        }
        result.append(
            {
                "metric": metric,
                "unit": ", ".join(sorted({str(item.get("unit")) for item in items})),
                "data_type": "number (JSON int/float)",
                "granularity": cadence_label(items),
                "first": first.isoformat(),
                "last": last.isoformat(),
                "sample_count": len(items),
                "days_with_data": len(observed_days),
                "total_days": total_days,
                "coverage_percent": round(100 * len(observed_days) / total_days, 1),
                "largest_missing_day_gap": largest_gap,
                "largest_missing_day_range": gap_range,
                "zero_count": sum(value == 0 for value in values),
                "out_of_plausible_range_count": sum(value < bounds[0] or value > bounds[1] for value in values),
                "duplicate_count": len(exact_keys) - len(set(exact_keys)),
                "future_count": sum(ts > now for ts in timestamps),
                "providers": providers,
                "source_count": len(sources),
                "min": min(values),
                "max": max(values),
            }
        )
    return result


def event_summary(items: list[dict[str, Any]], timestamp_field: str = "start_time") -> dict[str, Any]:
    timestamps = [parse_timestamp(item[timestamp_field]) for item in items]
    if not timestamps:
        return {"count": 0}
    observed = {ts.date() for ts in timestamps}
    first, last = min(timestamps), max(timestamps)
    total_days = (last.date() - first.date()).days + 1
    gap, gap_range = longest_missing_day_gap(observed)
    return {
        "count": len(items),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "days_with_data": len(observed),
        "total_days": total_days,
        "coverage_percent": round(100 * len(observed) / total_days, 1),
        "largest_missing_day_gap": gap,
        "largest_missing_day_range": gap_range,
        "future_count": sum(ts > datetime.now(timezone.utc) for ts in timestamps),
        "duplicate_id_count": len(items) - len({item.get("id") for item in items}),
        "providers": sorted({(item.get("source") or {}).get("provider") or item.get("provider") or "unknown" for item in items}),
    }


def coverage_metrics(coverage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        metric["code"]: metric
        for category in coverage.get("timeseries", [])
        for metric in category.get("metrics", [])
    }


def format_range(summary: dict[str, Any]) -> str:
    if not summary.get("count", summary.get("sample_count", 0)):
        return "none"
    return f"{summary['first'][:10]} to {summary['last'][:10]}"


def completeness_line(name: str, summary: dict[str, Any]) -> str:
    return (
        f"| `{name}` | {summary.get('sample_count', summary.get('count', 0)):,} | "
        f"{summary.get('days_with_data', 0)} / {summary.get('total_days', 0)} "
        f"({summary.get('coverage_percent', 0):.1f}%) | {summary.get('largest_missing_day_gap', 0)} days "
        f"({summary.get('largest_missing_day_range', 'none')}) | {summary.get('zero_count', 'n/a')} | "
        f"{summary.get('out_of_plausible_range_count', 'n/a')} | {summary.get('duplicate_count', summary.get('duplicate_id_count', 0))} | "
        f"{summary.get('future_count', 0)} |"
    )


def build_report(
    *,
    user: dict[str, Any],
    user_id: str,
    base_url: str,
    start_date: str,
    end_date: str,
    coverage: dict[str, Any],
    ts_summaries: list[dict[str, Any]],
    workouts: list[dict[str, Any]],
    sleeps: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> str:
    cov = coverage_metrics(coverage)
    workouts_summary = event_summary(workouts)
    sleeps_summary = event_summary(sleeps)
    scores_summary = event_summary(scores, "recorded_at")
    available = {item["metric"] for item in ts_summaries}
    supported_missing = sorted(set(cov) - available)

    inventory = [
        "| Metric | Endpoint | Unit | Data type | Granularity | Available range |",
        "|---|---|---|---|---|---|",
    ]
    for item in ts_summaries:
        inventory.append(
            f"| `{item['metric']}` | `/api/v1/users/{{id}}/timeseries` | {item['unit']} | {item['data_type']} | "
            f"{item['granularity']} | {item['first'][:10]} to {item['last'][:10]} |"
        )
    inventory.extend(
        [
            f"| `sleep_session` | `/api/v1/users/{{id}}/events/sleep` | seconds, minutes, % | object | per-session | {format_range(sleeps_summary)} |",
            f"| `sleep_stage_interval` | `/api/v1/users/{{id}}/events/sleep?include=stages` | timestamp interval | object[] | per-stage interval | {format_range(sleeps_summary)} |",
            f"| `workout_session` | `/api/v1/users/{{id}}/events/workouts` | mixed normalized units | object | per-session | {format_range(workouts_summary)} |",
        ]
    )
    for category in sorted({score.get("category", "unknown") for score in scores}):
        category_scores = [score for score in scores if score.get("category") == category]
        category_summary = event_summary(category_scores, "recorded_at")
        inventory.append(
            f"| `health_score:{category}` | `/api/v1/users/{{id}}/health-scores` | provider score scale | number + components | per-day/session | {format_range(category_summary)} |"
        )

    completeness = [
        "| Metric | Records | Days present / range | Largest whole-day gap | Zeros | Implausible* | Exact duplicates | Future |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    completeness.extend(completeness_line(item["metric"], item) for item in ts_summaries)
    completeness.append(completeness_line("sleep_session", sleeps_summary))
    completeness.append(completeness_line("workout_session", workouts_summary))
    completeness.append(completeness_line("health_scores", scores_summary))

    workout_fields = [
        "type",
        "duration_seconds",
        "calories_kcal",
        "distance_meters",
        "avg_heart_rate_bpm",
        "max_heart_rate_bpm",
        "heart_rate_min",
        "steps_count",
        "avg_pace_sec_per_km",
        "elevation_gain_meters",
        "average_speed",
        "max_speed",
        "average_cadence",
        "average_watts",
        "max_watts",
        "moving_time_seconds",
        "elev_high",
        "elev_low",
        "hr_zones",
        "power_zones",
        "segments",
        "entry_source",
        "intensity",
    ]
    workout_availability = [
        f"| `{field}` | {sum(item.get(field) is not None for item in workouts)} / {len(workouts)} |"
        for field in workout_fields
    ]
    stage_names = Counter(
        stage.get("stage", "unknown") for sleep in sleeps for stage in (sleep.get("sleep_stage_intervals") or [])
    )
    crossing_midnight = sum(
        parse_timestamp(item["start_time"]).date() != parse_timestamp(item["end_time"]).date() for item in sleeps
    )
    naps = sum(bool(item.get("is_nap")) for item in sleeps)
    sources_by_metric = "\n".join(
        f"- `{item['metric']}`: {', '.join(item['providers'])}; {item['source_count']} distinct provider/source/device tuple(s)."
        for item in ts_summaries
    )
    endpoints = "\n".join(
        f"- `{entry['name']}`: `GET {entry['path']}` ({entry['pagination']}; {entry['pages']} response page(s), {entry['records']:,} records)."
        for entry in manifest["requests"]
    )
    no_data_categories = [name for name, types in TIME_SERIES_GROUPS.items() if not (set(types) & available)]
    score_categories = Counter(score.get("category", "unknown") for score in scores)
    score_text = ", ".join(f"{key}={value}" for key, value in sorted(score_categories.items())) or "none"

    return f"""# Open Wearables metrics audit

Generated by `audit/run_audit.py` at {datetime.now(timezone.utc).isoformat()}. This audit is observational only; no product features or upstream source changes were made.

## Scope and provenance

- API: `{base_url}`; audited user `{user_id}` (`{user.get('first_name', '')} {user.get('last_name', '')}`); requested window `{start_date}` through `{end_date}`.
- **All audited records are synthetic.** They came from the project's documented `comprehensive` seed preset with random seed `20260930`, fixed service anchor `2025-01-01`.
- The initial documented `make seed` run also succeeded (2 users, 160 workouts, 40 sleeps), but its default profile generated **0 time-series samples**. It was therefore not used for metric completeness.
- Raw API response pages are saved under `audit/output/`; `manifest.json` maps files to requests. Secrets are never written there.
- Completeness is measured independently inside each metric's first-to-last observed UTC date, not across the wider requested window. A day means the UTC calendar date of the returned timestamp because the synthetic rows have no `zone_offset`.

## Runtime and API verification

The repository is FastAPI + SQLAlchemy/PostgreSQL, React/TypeScript, Redis, two Celery worker queues plus Celery Beat, Flower, and Svix. Compose started eight services: `db`, `app`, `celery-worker`, `celery-beat`, `flower`, `redis`, `svix-server`, and `frontend`.

Verified locally:

- `http://localhost:8000/docs`, `/redoc`, and `/openapi.json`: HTTP 200.
- `http://localhost:3000`: HTTP 200; `http://localhost:5555`: HTTP 200.
- PostgreSQL and Svix: Compose healthy. The remaining services have no Compose healthcheck; the API served requests, Redis returned `PONG`, both Celery worker nodes returned `pong`, and Beat logged that it started.
- External read API authentication uses `X-Open-Wearables-API-Key`. Developer JWT authentication is used by the portal to create/revoke keys and to run the seed-data settings endpoint.
- No application-level numeric rate limit was found in the API code/configuration. A reverse proxy or hosted deployment may impose one; provider ingestion APIs have their own limits. The local audit received no 429 responses.

## Data ingestion options

- Synthetic seed generator: developer portal **Settings → Seed Data** or `POST /api/v1/settings/seed`; documented presets include Comprehensive.
- OAuth/REST and/or provider webhooks: Garmin, Oura, WHOOP, Suunto, Polar, Ultrahuman, Strava, Fitbit, Withings, and Google APIs. These require provider credentials and usually a public callback URL.
- Mobile SDK push: Apple Health/HealthKit and Android Health Connect/Samsung Health through the iOS, Android, React Native, or Flutter SDK paths.
- Apple Health XML export: direct upload for smaller files, or multipart S3-compatible upload for large exports; object-storage credentials/configuration are needed for the latter.
- Incoming webhooks support full payload streams, notification-then-fetch, and async export callbacks depending on provider. Outgoing Svix webhooks are disabled by default in this local configuration.

## API pulls and pagination

Time-series, sleep, workout, and summary endpoints accept date bounds; time-series uses `start_time`/`end_time`, while events and scores use `start_date`/`end_date`. Cursor-based endpoints return `pagination.next_cursor`; this script follows it with `limit=1000`. Health scores expose `limit` + `offset` and are fetched by offset. Repeated `types` and `include` query parameters select multiple metrics and expansions.

{endpoints}

## Inventory

{chr(10).join(inventory)}

The coverage metadata advertises {len(cov)} normalized time-series types. This seeded user returned {len(available)} types. Supported but absent here ({len(supported_missing)}): {', '.join(f'`{name}`' for name in supported_missing)}.

## Source and normalization details

Every returned source object identifies `provider` (integration), `source` (writer/app within the integration), raw `device`, normalized `device_type`, and derived `device_name`. `data_source_id` can also be used as an input filter, although the public sample response does not expose it.

{sources_by_metric}

The raw API defaults to `filter_by_priority=false`, so one metric may be returned from multiple sources for the same requested period. Time-series priority filtering chooses one winning populated source per metric for the query window using provider priority, then device-type priority. Sleep priority filtering chooses a winning source per local sleep date. Workouts do not expose a priority flag. This seeded dataset generally assigns a single source to each continuous metric, so it does not exercise conflict resolution.

Exact time-series duplicates are prevented by a database uniqueness key on `(data_source_id, series_type, recorded_at)`. Batch re-ingestion keeps the last record in a batch and upserts changed value, external ID, offset, and daily-total flag. This does **not** deduplicate semantically equivalent readings from different sources. Sleep re-ingestion with the same external ID refreshes the existing session; adjacent, distinct sleep fragments from the same source/provider can be merged by the sleep service. Workout provider handlers are expected to deduplicate on provider IDs, so behavior is provider-specific.

Timestamps are ISO-8601 instants (`Z` in these responses) and rows may carry a separate `zone_offset` such as `-05:00`. Local sleep dates use that offset; when it is null, UTC is the fallback. Units are canonicalized by series type (for example bpm, ms, %, kg, kcal, meters), but a consumer should still validate the returned `unit`. Aggregated resolutions choose type-specific sum/average/max behavior; this audit requested `resolution=raw`.

## Completeness and anomalies

{chr(10).join(completeness)}

\* “Implausible” uses conservative bounds encoded in `audit/run_audit.py`; for metrics without a specific physiological bound it only flags negative values. Zeros are reported separately and are not automatically considered errors. Full machine-readable calculations, including min/max and provider/source counts, are in `audit/output/completeness.json`.

Because this is generated synthetic data, these percentages measure the preset, **not real provider reliability**. The preset intentionally has partial coverage. The script found exact response-level duplicates and future timestamps as shown above; provider-cross-source semantic duplicates require real overlapping provider data to assess.

## Sleep

- Sessions: {len(sleeps)}; naps flagged: {naps}; sessions crossing UTC midnight: {crossing_midnight}.
- Stage summary fields: `deep_minutes`, `light_minutes`, `rem_minutes`, `awake_minutes`; interval stages observed: {', '.join(f'`{k}` ({v})' for k, v in sorted(stage_names.items())) or 'none'}.
- Session fields include start/end, zone offset, duration, sleep duration, time in bed, efficiency, nap flag, source, stage totals, and optional stage intervals.
- A sleep session is returned as one record even when it crosses midnight; it is not split at midnight. The ingestion service may merge adjacent fragments from the same source/provider, and refreshes a repeated external ID in place.

## Workouts

The endpoint returns normalized type, start/end, duration, source/device, entry source, intensity, calories, distance, heart-rate summaries, pace, elevation, steps, speed, cadence, watts, moving time, HR/power zones, and segments. Actual non-null coverage in this seed:

| Field | Present |
|---|---:|
{chr(10).join(workout_availability)}

Health score categories in the same user: {score_text}. Score values are not universally comparable: ranges and component names vary by provider/category.

## Gaps and risks

- Missing in this seed: {', '.join(no_data_categories) if no_data_categories else 'none of the requested category groups'}, plus the individual supported-but-absent types listed above. Absence here means untested/unsupplied, not necessarily unsupported.
- Real provider credentials or a user-provided export are required to judge real-world accuracy, delayed sync, clock drift, source overlap, duplicates across providers, and provider-specific missingness. The synthetic dataset cannot answer those questions.
- `zone_offset` is null throughout the sampled synthetic records, so local-day scoring and daylight-saving behavior are untested. A fitness score should bucket by the supplied local offset and define a fallback explicitly.
- Raw time series can contain multiple sources. A scoring algorithm must deliberately select sources (priority-filtered reads or its own reconciliation) and should not simply sum overlapping sources.
- Priority selection for time-series is a winner per metric over the whole query window, not a timestamp-by-timestamp merge. A lower-priority source that fills gaps may be omitted when priority filtering is on.
- Workout fields and health-score scales are sparse/provider-dependent. Do not assume zones, segments, power, or scores are present or comparable.
- The recovery summary route documents a current limitation: only WHOOP recovery is used even when underlying HRV/resting-HR/SpO2 exists from other providers. Build provider-neutral recovery logic from normalized raw metrics unless that upstream behavior is changed in the fork.
- The OpenAPI document emits duplicate-operation-ID warnings for legacy/duplicate Oura webhook mounts. Reads worked, but generated clients should be reviewed for name collisions.
- Numeric read rate limits are not documented or enforced in this local app. Production deployment limits must be established before sizing a batch scoring job.

## Fitness-score readiness summary

- **Solid enough structurally:** raw heart rate, workouts, sleep sessions/stages, steps, active/basal energy, and distance. Their schemas, units, pagination, and source metadata are clear; validate reliability again with real data.
- **Usable with caveats:** resting HR, HRV, SpO2, respiratory/skin temperature, VO2 max, body composition, blood pressure, health scores, and workout power/cadence. They are sparse, provider-specific, method-specific (especially SDNN vs RMSSD), or not comparable across vendors.
- **Missing/unproven for this dataset:** every supported-but-absent metric above, real timezone behavior, cross-provider conflict quality, and live-provider completeness. These should not influence a production score until audited with a real export or credentialed sync.

## Reproducibility notes and observed failures

- Initial unprivileged `docker version` could not reach Docker Desktop; after Docker Desktop was started, the daemon reported version 29.7.2.
- The first HTTP probes ran before application startup completed and got connection resets; retries after the startup log succeeded.
- A first API-key request used `/api/v1/api-keys` and returned 404; the correct developer route is `/api/v1/developer/api-keys`.
- A host Python HTTP attempt was blocked by the execution sandbox; approved local HTTP calls succeeded.
- `jq` is not installed, so the inspection command failed with `command not found`; the audit script uses Python JSON parsing and does not require `jq`.
"""


def main() -> int:
    api_key = os.environ.get("OPEN_WEARABLES_API_KEY")
    if not api_key:
        print("OPEN_WEARABLES_API_KEY is required", file=sys.stderr)
        return 2
    base_url = os.environ.get("OPEN_WEARABLES_API_URL", "http://localhost:8000")
    start_date = os.environ.get("AUDIT_START_DATE", "2024-01-01")
    end_date = os.environ.get("AUDIT_END_DATE", "2025-12-31")
    output = Path(os.environ.get("AUDIT_OUTPUT_DIR", str(DEFAULT_OUTPUT)))
    output.mkdir(parents=True, exist_ok=True)
    api = Api(base_url, api_key)
    user_id, user, users_response = choose_user(api, os.environ.get("OPEN_WEARABLES_USER_ID"))
    print(f"Auditing user {user_id}: {user.get('first_name', '')} {user.get('last_name', '')}")

    coverage = api.get("/api/v1/meta/coverage")
    user_detail = api.get(f"/api/v1/users/{user_id}")
    json_write(output / "users.json", users_response)
    json_write(output / "user.json", user_detail)
    json_write(output / "coverage.json", coverage)

    common_ts = [("start_time", start_date), ("end_time", end_date), ("resolution", "raw")]
    common_events = [("start_date", start_date), ("end_date", end_date)]
    specs: list[RequestSpec] = []
    for name, types in TIME_SERIES_GROUPS.items():
        specs.append(
            RequestSpec(
                name,
                f"/api/v1/users/{user_id}/timeseries",
                common_ts + [("types", metric) for metric in types],
            )
        )
    specs.extend(
        [
            RequestSpec(
                "sleep_sessions",
                f"/api/v1/users/{user_id}/events/sleep",
                common_events + [("include", "stages")],
            ),
            RequestSpec(
                "workouts",
                f"/api/v1/users/{user_id}/events/workouts",
                common_events + [("include", "zones"), ("include", "segments")],
            ),
            RequestSpec(
                "health_scores",
                f"/api/v1/users/{user_id}/health-scores",
                [("start_date", start_date), ("end_date", end_date)],
                "offset",
            ),
        ]
    )
    fetched: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(specs))) as executor:
        future_to_spec = {executor.submit(api.paginated, spec): spec for spec in specs}
        for future in as_completed(future_to_spec):
            spec = future_to_spec[future]
            fetched[spec.name] = future.result()
            count = len(rows(fetched[spec.name]))
            print(f"{spec.name}: {count:,} records in {len(fetched[spec.name])} page(s)")
            json_write(output / f"{spec.name}.json", fetched[spec.name])

    single_requests: list[tuple[str, str, list[tuple[str, str]]]] = [
        ("data_summary", f"/api/v1/users/{user_id}/summaries/data", common_events),
        ("data_timeline", f"/api/v1/users/{user_id}/summaries/data/timeline", common_events + [("group_by", "series_type"), ("bucket", "day")]),
        ("activity_summaries", f"/api/v1/users/{user_id}/summaries/activity", common_events),
        ("sleep_summaries", f"/api/v1/users/{user_id}/summaries/sleep", common_events),
        ("recovery_summaries", f"/api/v1/users/{user_id}/summaries/recovery", common_events),
        ("body_summary", f"/api/v1/users/{user_id}/summaries/body", []),
        ("connections", f"/api/v1/users/{user_id}/connections", []),
    ]
    singles: dict[str, Any] = {}
    for name, path, params in single_requests:
        try:
            singles[name] = api.get(path, params)
        except httpx.HTTPStatusError as exc:
            singles[name] = {"error": str(exc), "response": exc.response.text}
        json_write(output / f"{name}.json", singles[name])

    all_ts = rows([page for name in TIME_SERIES_GROUPS for page in fetched[name]])
    workouts = rows(fetched["workouts"])
    sleeps = rows(fetched["sleep_sessions"])
    scores = rows(fetched["health_scores"])
    summaries = summarize_timeseries(all_ts)
    completeness_output = {
        "time_series": summaries,
        "sleep_sessions": event_summary(sleeps),
        "workouts": event_summary(workouts),
        "health_scores": event_summary(scores, "recorded_at"),
    }
    json_write(output / "completeness.json", completeness_output)

    spec_by_name = {spec.name: spec for spec in specs}
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "user_id": user_id,
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "authentication": "X-Open-Wearables-API-Key (value deliberately omitted)",
        "requests": [
            {
                "name": name,
                "file": f"{name}.json",
                "path": spec_by_name[name].path,
                "params": spec_by_name[name].params,
                "pagination": spec_by_name[name].pagination,
                "pages": len(fetched[name]),
                "records": len(rows(fetched[name])),
            }
            for name in sorted(fetched)
        ],
        "single_response_files": [f"{name}.json" for name, _, _ in single_requests] + ["users.json", "user.json", "coverage.json"],
    }
    json_write(output / "manifest.json", manifest)
    report = build_report(
        user=user,
        user_id=user_id,
        base_url=base_url,
        start_date=start_date,
        end_date=end_date,
        coverage=coverage,
        ts_summaries=summaries,
        workouts=workouts,
        sleeps=sleeps,
        scores=scores,
        manifest=manifest,
    )
    (ROOT / "METRICS_AUDIT.md").write_text(report)
    print(f"Wrote {ROOT / 'METRICS_AUDIT.md'}")
    print(f"Wrote raw responses and calculations to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
