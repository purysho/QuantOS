from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode
import requests

from quantos.artifacts import ArtifactRef, SourceArtifactStore
from quantos.research_radar import DiscoveryItem, make_discovery_id
from quantos.security import guarded


ARXIV_FINANCE_CATEGORIES = (
    "q-fin.CP",
    "q-fin.EC",
    "q-fin.GN",
    "q-fin.MF",
    "q-fin.PM",
    "q-fin.PR",
    "q-fin.RM",
    "q-fin.ST",
    "q-fin.TR",
)

_CATEGORY = re.compile(r"^[a-z][a-z-]*\.[A-Za-z][A-Za-z-]*$")
_VERSION = re.compile(r"v\d+$")

Transport = Callable[[str, dict[str, str], float], tuple[bytes, str]]


class ArxivRadarError(ValueError):
    pass


@dataclass(frozen=True)
class ArxivRadarFetch:
    query_url: str
    fetched_at: datetime
    items: tuple[DiscoveryItem, ...]
    feed_artifact: ArtifactRef | None


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> tuple[bytes, str]:
    guarded(url, "arxiv-radar")
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ArxivRadarError("arXiv metadata request failed") from exc
    if response.status_code != 200:
        raise ArxivRadarError(
            f"arXiv metadata request failed with HTTP {response.status_code}"
        )
    media_type = (
        response.headers.get("content-type", "application/atom+xml")
        .split(";", 1)[0]
        .strip()
    )
    return response.content, media_type


class ArxivRadarAdapter:
    """Metadata-only arXiv discovery adapter with legacy-API rate limiting."""

    base_url = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 15.0,
        min_interval_seconds: float = 3.0,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ArxivRadarError("user_agent is required")
        if timeout_seconds <= 0:
            raise ArxivRadarError("timeout_seconds must be positive")
        if min_interval_seconds < 3.0:
            raise ArxivRadarError(
                "arXiv legacy API interval must be at least 3 seconds"
            )
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
        categories: tuple[str, ...] = ARXIV_FINANCE_CATEGORIES,
        max_results: int = 50,
        start: int = 0,
        artifact_store: SourceArtifactStore | None = None,
    ) -> ArxivRadarFetch:
        url = self.build_url(
            categories=categories,
            max_results=max_results,
            start=start,
        )
        self._throttle()
        body, media_type = self.transport(
            url,
            {"user-agent": self.user_agent},
            self.timeout_seconds,
        )
        self._last_request_at = self.clock()

        if not body:
            raise ArxivRadarError("arXiv returned an empty feed")
        fetched_at = datetime.now(timezone.utc)
        artifact = (
            artifact_store.put(
                source_uri=url,
                content=body,
                fetched_at=fetched_at,
                media_type=media_type or "application/atom+xml",
            )
            if artifact_store is not None
            else None
        )
        items = self.parse_feed(
            body,
            fetched_at=fetched_at,
            source_uri=url,
            feed_artifact_id=artifact.artifact_id if artifact else None,
        )
        return ArxivRadarFetch(
            query_url=url,
            fetched_at=fetched_at,
            items=items,
            feed_artifact=artifact,
        )

    @classmethod
    def build_url(
        cls,
        *,
        categories: tuple[str, ...],
        max_results: int,
        start: int = 0,
    ) -> str:
        if not categories:
            raise ArxivRadarError("at least one explicit category is required")
        if len(set(categories)) != len(categories):
            raise ArxivRadarError("duplicate categories are not allowed")
        invalid = [category for category in categories if not _CATEGORY.fullmatch(category)]
        if invalid:
            raise ArxivRadarError(
                "invalid arXiv categories: " + ", ".join(invalid)
            )
        if not 1 <= max_results <= 100:
            raise ArxivRadarError("prototype max_results must be between 1 and 100")
        if start < 0:
            raise ArxivRadarError("start cannot be negative")

        query = " OR ".join(f"cat:{category}" for category in categories)
        params = urlencode(
            {
                "search_query": query,
                "start": start,
                "max_results": max_results,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
            }
        )
        return f"{cls.base_url}?{params}"

    @staticmethod
    def parse_feed(
        body: bytes,
        *,
        fetched_at: datetime,
        source_uri: str,
        feed_artifact_id: str | None,
    ) -> tuple[DiscoveryItem, ...]:
        if fetched_at.tzinfo is None:
            raise ArxivRadarError("fetched_at must be timezone-aware")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ArxivRadarError("invalid Atom feed") from exc

        atom = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(f"{atom}entry")
        items: list[DiscoveryItem] = []
        for entry in entries:
            external_url = _required_text(entry, f"{atom}id")
            external_id = external_url.rstrip("/").split("/")[-1]
            canonical_id = _VERSION.sub("", external_id)
            title = _normalize(_required_text(entry, f"{atom}title"))
            summary = _normalize(_required_text(entry, f"{atom}summary"))
            published_at = _parse_datetime(
                _required_text(entry, f"{atom}published")
            )
            updated_at = _parse_datetime(
                _required_text(entry, f"{atom}updated")
            )
            authors = tuple(
                _normalize(name.text or "")
                for author in entry.findall(f"{atom}author")
                if (name := author.find(f"{atom}name")) is not None
                and _normalize(name.text or "")
            )
            categories = tuple(
                dict.fromkeys(
                    category.attrib["term"]
                    for category in entry.findall(f"{atom}category")
                    if category.attrib.get("term")
                )
            )
            discovery_id = make_discovery_id(
                provider="arxiv",
                canonical_id=canonical_id,
                updated_at=updated_at,
            )
            items.append(
                DiscoveryItem(
                    discovery_id=discovery_id,
                    provider="arxiv",
                    external_id=external_id,
                    canonical_id=canonical_id,
                    title=title,
                    summary=summary,
                    authors=authors,
                    categories=categories,
                    published_at=published_at,
                    updated_at=updated_at,
                    discovered_at=fetched_at,
                    source_uri=external_url,
                    feed_artifact_id=feed_artifact_id,
                )
            )
        return tuple(items)

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self.clock() - self._last_request_at
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            self.sleeper(remaining)

    
def _required_text(entry: ET.Element, tag: str) -> str:
    node = entry.find(tag)
    if node is None or not (node.text or "").strip():
        raise ArxivRadarError(f"Atom entry missing {tag}")
    return (node.text or "").strip()


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ArxivRadarError(f"invalid arXiv datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise ArxivRadarError("arXiv datetime is missing timezone")
    return parsed