"""Stage 15.1 — Crossref DOI metadata radar (journal research discovery).

Metadata-only, like the arXiv radar: journal articles found through the
Crossref REST API enter the Research Radar as discovery items and then the
existing triage -> review-queue -> verification workflow. Nothing becomes a
claim automatically.

Crossref etiquette is enforced: a contact address is mandatory (it selects
the "polite" pool and lets Crossref reach the operator), requests are
rate-limited, and the exact response bytes are archived as an artifact.
Partial publication dates are not padded silently; their precision is
recorded as a category tag.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable
from urllib.parse import urlencode

import requests

from quantos.artifacts import ArtifactRef, SourceArtifactStore
from quantos.research_radar import DiscoveryItem, make_discovery_id

# Core finance / portfolio journals by print ISSN. Extend deliberately.
FINANCE_JOURNAL_ISSNS = (
    "0022-1082",  # The Journal of Finance
    "0304-405X",  # Journal of Financial Economics
    "0893-9454",  # The Review of Financial Studies
    "0022-1090",  # Journal of Financial and Quantitative Analysis
    "0095-4918",  # The Journal of Portfolio Management
    "0015-198X",  # Financial Analysts Journal
)

_ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_TAGS = re.compile(r"<[^>]+>")

Transport = Callable[[str, dict[str, str], float], tuple[bytes, str]]


class CrossrefRadarError(ValueError):
    pass


@dataclass(frozen=True)
class CrossrefRadarFetch:
    query_url: str
    fetched_at: datetime
    items: tuple[DiscoveryItem, ...]
    feed_artifact: ArtifactRef | None


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> tuple[bytes, str]:
    try:
        response = requests.get(url, headers=headers, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise CrossrefRadarError("Crossref metadata request failed") from exc
    if response.status_code != 200:
        raise CrossrefRadarError(
            f"Crossref metadata request failed with HTTP {response.status_code}"
        )
    media_type = (
        response.headers.get("content-type", "application/json")
        .split(";", 1)[0]
        .strip()
    )
    return response.content, media_type


class CrossrefRadarAdapter:
    base_url = "https://api.crossref.org/works"
    SELECT = (
        "DOI,title,author,abstract,published,indexed,container-title,"
        "subject,URL,type,ISSN"
    )

    def __init__(
        self,
        *,
        mailto: str,
        user_agent: str,
        timeout_seconds: float = 20.0,
        min_interval_seconds: float = 1.0,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if "@" not in mailto or not mailto.strip():
            raise CrossrefRadarError("a contact mailto address is required")
        if not user_agent.strip():
            raise CrossrefRadarError("user_agent is required")
        if timeout_seconds <= 0:
            raise CrossrefRadarError("timeout_seconds must be positive")
        if min_interval_seconds < 1.0:
            raise CrossrefRadarError("Crossref interval must be at least 1 second")
        self.mailto = mailto.strip()
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self.transport = transport or _default_transport
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._last_request_at: float | None = None

    def fetch(
        self,
        *,
        issns: tuple[str, ...] = FINANCE_JOURNAL_ISSNS,
        from_index_date: date,
        query: str | None = None,
        max_results: int = 50,
        artifact_store: SourceArtifactStore | None = None,
    ) -> CrossrefRadarFetch:
        url = self.build_url(
            issns=issns,
            from_index_date=from_index_date,
            query=query,
            max_results=max_results,
            mailto=self.mailto,
        )
        self._throttle()
        body, media_type = self.transport(
            url,
            {"user-agent": f"{self.user_agent} (mailto:{self.mailto})"},
            self.timeout_seconds,
        )
        self._last_request_at = self.clock()
        if not body:
            raise CrossrefRadarError("Crossref returned an empty response")
        fetched_at = datetime.now(timezone.utc)
        artifact = (
            artifact_store.put(
                source_uri=url,
                content=body,
                fetched_at=fetched_at,
                media_type=media_type or "application/json",
            )
            if artifact_store is not None
            else None
        )
        items = self.parse_response(
            body,
            fetched_at=fetched_at,
            feed_artifact_id=artifact.artifact_id if artifact else None,
        )
        return CrossrefRadarFetch(
            query_url=url,
            fetched_at=fetched_at,
            items=items,
            feed_artifact=artifact,
        )

    @classmethod
    def build_url(
        cls,
        *,
        issns: tuple[str, ...],
        from_index_date: date,
        query: str | None,
        max_results: int,
        mailto: str,
    ) -> str:
        if not issns:
            raise CrossrefRadarError("at least one explicit journal ISSN is required")
        if len(set(issns)) != len(issns):
            raise CrossrefRadarError("duplicate ISSNs are not allowed")
        invalid = [item for item in issns if not _ISSN.fullmatch(item)]
        if invalid:
            raise CrossrefRadarError("invalid ISSNs: " + ", ".join(invalid))
        if not 1 <= max_results <= 100:
            raise CrossrefRadarError("prototype max_results must be between 1 and 100")
        filters = [
            f"from-index-date:{from_index_date.isoformat()}",
            "type:journal-article",
            *(f"issn:{item}" for item in issns),
        ]
        params = {
            "filter": ",".join(filters),
            "rows": max_results,
            "sort": "indexed",
            "order": "desc",
            "select": cls.SELECT,
            "mailto": mailto,
        }
        if query is not None:
            if not query.strip():
                raise CrossrefRadarError("query cannot be blank")
            params["query.bibliographic"] = query.strip()
        return f"{cls.base_url}?{urlencode(params)}"

    @staticmethod
    def parse_response(
        body: bytes,
        *,
        fetched_at: datetime,
        feed_artifact_id: str | None,
    ) -> tuple[DiscoveryItem, ...]:
        if fetched_at.tzinfo is None:
            raise CrossrefRadarError("fetched_at must be timezone-aware")
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CrossrefRadarError("invalid Crossref JSON") from exc
        if document.get("status") != "ok" or document.get("message-type") != "work-list":
            raise CrossrefRadarError("unexpected Crossref response envelope")
        items: list[DiscoveryItem] = []
        seen: set[str] = set()
        for work in document.get("message", {}).get("items", []):
            doi = str(work.get("DOI", "")).strip()
            if not _DOI.fullmatch(doi):
                raise CrossrefRadarError(f"Crossref work has invalid DOI {doi!r}")
            canonical = doi.lower()
            if canonical in seen:
                raise CrossrefRadarError(f"duplicate DOI in one response: {doi}")
            seen.add(canonical)
            titles = [_normalize(t) for t in work.get("title", []) if _normalize(t)]
            if not titles:
                raise CrossrefRadarError(f"Crossref work {doi} has no title")
            indexed = work.get("indexed", {}).get("date-time")
            if not indexed:
                raise CrossrefRadarError(f"Crossref work {doi} has no index time")
            updated_at = _parse_datetime(indexed)
            published_at, precision = _published(work.get("published"), doi)
            authors = tuple(
                name
                for author in work.get("author", [])
                if (
                    name := _normalize(
                        " ".join(
                            part
                            for part in (author.get("given"), author.get("family"))
                            if part
                        )
                        or author.get("name", "")
                    )
                )
            )
            categories = tuple(
                dict.fromkeys(
                    [
                        *(f"journal:{_normalize(t)}" for t in work.get("container-title", [])),
                        *(f"subject:{_normalize(s)}" for s in work.get("subject", [])),
                        *(f"issn:{i}" for i in work.get("ISSN", [])),
                        f"published-precision:{precision}",
                    ]
                )
            )
            items.append(
                DiscoveryItem(
                    discovery_id=make_discovery_id(
                        provider="crossref",
                        canonical_id=canonical,
                        updated_at=updated_at,
                    ),
                    provider="crossref",
                    external_id=doi,
                    canonical_id=canonical,
                    title=titles[0],
                    summary=_normalize(_TAGS.sub(" ", work.get("abstract", ""))),
                    authors=authors,
                    categories=categories,
                    published_at=published_at,
                    updated_at=updated_at,
                    discovered_at=fetched_at,
                    source_uri=work.get("URL") or f"https://doi.org/{doi}",
                    feed_artifact_id=feed_artifact_id,
                )
            )
        return tuple(items)

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self.min_interval_seconds - (self.clock() - self._last_request_at)
        if remaining > 0:
            self.sleeper(remaining)


def _published(value: object, doi: str) -> tuple[datetime, str]:
    parts = (value or {}).get("date-parts", [[]])[0] if isinstance(value, dict) else []
    if not parts or parts[0] is None:
        raise CrossrefRadarError(f"Crossref work {doi} has no publication date")
    precision = ("year", "month", "day")[len(parts[:3]) - 1]
    year, month, day = (list(parts[:3]) + [1, 1])[:3]
    return datetime(int(year), int(month), int(day), tzinfo=timezone.utc), precision


def _normalize(value: str) -> str:
    return " ".join(str(value).split())


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CrossrefRadarError(f"invalid Crossref datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise CrossrefRadarError("Crossref datetime is missing timezone")
    return parsed
