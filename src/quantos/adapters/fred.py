from __future__ import annotations

import json
from datetime import datetime, time, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quantos.models import Event


class FREDAdapterError(ValueError):
    pass


def _safe_knowledge_time(date_text: str) -> datetime:
    """Use end-of-day UTC when FRED only gives a vintage date, not a release time."""
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise FREDAdapterError(f"invalid realtime_start: {date_text}") from exc
    return datetime.combine(day, time(23, 59, 59, 999999), tzinfo=timezone.utc)


class FREDVintageAdapter:
    base_url = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, *, api_key: str, timeout_seconds: float = 10.0) -> None:
        if not api_key:
            raise FREDAdapterError("FRED API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_series_as_of(
        self,
        *,
        series_id: str,
        vintage_date: str,
    ) -> tuple[Event, ...]:
        params = urlencode(
            {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "realtime_start": vintage_date,
                "realtime_end": vintage_date,
            }
        )
        request = Request(
            f"{self.base_url}?{params}",
            headers={"Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        return self.events_from_payload(payload, series_id=series_id)

    @staticmethod
    def events_from_payload(
        payload: dict[str, Any],
        *,
        series_id: str,
    ) -> tuple[Event, ...]:
        observations = payload.get("observations")
        if not isinstance(observations, list):
            raise FREDAdapterError("FRED payload missing observations")

        events: list[Event] = []
        for row in observations:
            realtime_start = row.get("realtime_start")
            observation_date = row.get("date")
            raw_value = row.get("value")
            if not realtime_start or not observation_date:
                raise FREDAdapterError("observation missing vintage/date fields")
            if raw_value in {None, "."}:
                continue

            try:
                value = float(raw_value)
                obs_day = datetime.strptime(observation_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise FREDAdapterError("invalid observation value/date") from exc

            event_time = datetime.combine(obs_day, time.min, tzinfo=timezone.utc)
            knowledge_time = _safe_knowledge_time(realtime_start)
            if event_time > knowledge_time:
                raise FREDAdapterError(
                    "observation date occurs after the reported vintage date"
                )

            events.append(
                Event(
                    event_id=(
                        f"fred:{series_id}:{observation_date}:{realtime_start}"
                    ),
                    entity_id=f"FRED:{series_id}",
                    event_type="macro.observation",
                    event_time=event_time,
                    knowledge_time=knowledge_time,
                    source_id=(
                        f"fred://series/{series_id}/"
                        f"{observation_date}?vintage={realtime_start}"
                    ),
                    payload={
                        "series_id": series_id,
                        "observation_date": observation_date,
                        "value": value,
                        "realtime_start": realtime_start,
                        "realtime_end": row.get("realtime_end"),
                        "knowledge_time_resolution": "date_only_conservative_eod_utc",
                    },
                )
            )

        return tuple(events)
