from __future__ import annotations

import json
from datetime import datetime, time, timezone
from typing import Any
from urllib.request import Request, urlopen

from quantos.models import Event


class SECAdapterError(ValueError):
    pass


def _parse_sec_time(value: str | None, fallback_date: str | None) -> datetime:
    if value:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    if fallback_date:
        try:
            day = datetime.strptime(fallback_date, "%Y-%m-%d").date()
            return datetime.combine(day, time.min, tzinfo=timezone.utc)
        except ValueError as exc:
            raise SECAdapterError(f"invalid SEC filing date: {fallback_date}") from exc

    raise SECAdapterError("SEC filing has neither a valid acceptance time nor filing date")


class SECSubmissionsAdapter:
    """Read SEC submissions and convert recent filings into point-in-time events.

    The adapter treats fetched content strictly as untrusted data. It does not
    interpret filing text or execute instructions from documents.
    """

    base_url = "https://data.sec.gov/submissions"

    def __init__(self, *, user_agent: str, timeout_seconds: float = 10.0) -> None:
        if "@" not in user_agent:
            raise SECAdapterError(
                "SEC user_agent must identify the application and include a contact email"
            )
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    def fetch(self, cik: int) -> tuple[Event, ...]:
        if cik <= 0:
            raise SECAdapterError("CIK must be positive")
        url = f"{self.base_url}/CIK{cik:010d}.json"
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        fetched_at = datetime.now(timezone.utc)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        return self.events_from_payload(payload, fetched_at=fetched_at, cik=cik)

    @staticmethod
    def events_from_payload(
        payload: dict[str, Any],
        *,
        fetched_at: datetime,
        cik: int | None = None,
    ) -> tuple[Event, ...]:
        if fetched_at.tzinfo is None:
            raise SECAdapterError("fetched_at must be timezone-aware")

        resolved_cik = cik
        if resolved_cik is None:
            raw_cik = payload.get("cik")
            if raw_cik is None:
                raise SECAdapterError("SEC payload missing CIK")
            resolved_cik = int(raw_cik)

        recent = payload.get("filings", {}).get("recent", {})
        accession_numbers = recent.get("accessionNumber", [])
        if not isinstance(accession_numbers, list):
            raise SECAdapterError("unexpected SEC recent filings structure")

        keys = (
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "act",
            "form",
            "fileNumber",
            "filmNumber",
            "items",
            "size",
            "isXBRL",
            "isInlineXBRL",
            "primaryDocument",
            "primaryDocDescription",
        )

        events: list[Event] = []
        for index, accession in enumerate(accession_numbers):
            row: dict[str, Any] = {"accessionNumber": accession}
            for key in keys:
                column = recent.get(key, [])
                row[key] = column[index] if isinstance(column, list) and index < len(column) else None

            accepted_at = _parse_sec_time(row.get("acceptanceDateTime"), row.get("filingDate"))
            if fetched_at < accepted_at:
                raise SECAdapterError(
                    "fetched_at precedes SEC acceptance time; clock or fixture is invalid"
                )

            events.append(
                Event(
                    entity_id=f"CIK:{resolved_cik:010d}",
                    event_type=f"sec.filing.{row.get('form') or 'UNKNOWN'}",
                    event_time=accepted_at,
                    knowledge_time=fetched_at,
                    source_id=f"sec://submissions/{resolved_cik:010d}/{accession}",
                    payload=row,
                )
            )

        return tuple(events)
