"""Stage 15.2 — institutional research and regulator feeds (NBER, Fed, SEC, BIS, ECB).

Metadata-only discovery from a frozen registry of RSS 2.0, RSS 1.0 (RDF)
and Atom feeds. Items enter the Research Radar exactly like arXiv and
Crossref items and go through triage and human review. Nothing becomes a
claim, and a regulator press release is a discovery lead, not a verified
fact.

Safety and point-in-time rules:

* only registered feeds are fetched, each through the egress guard and kill
  switch, with a contact User-Agent (SEC fair-access policy) and a rate limit;
* XML containing a DOCTYPE or entity declaration is refused (no entity
  expansion), and bodies over 5 MB are refused;
* exact response bytes are archived as a source artifact;
* feeds that carry no dates (NBER's new-papers feed) are stamped with the
  first time First Current saw the item, tagged
  ``published-precision:first-seen``, and never back-dated.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import requests

from quantos.artifacts import ArtifactRef, SourceArtifactStore
from quantos.research_radar import DiscoveryItem, make_discovery_id
from quantos.security import guarded

Transport = Callable[[str, dict[str, str], float], tuple[bytes, str]]

MAX_FEED_BYTES = 5_000_000
_TAGS = re.compile(r"<[^>]+>")
_NS_DC = "{http://purl.org/dc/elements/1.1/}"
_NS_ATOM = "{http://www.w3.org/2005/Atom}"


class FeedRadarError(ValueError):
    pass


@dataclass(frozen=True)
class FeedSource:
    source_id: str
    publisher: str
    kind: str
    url: str
    parser: str = "generic"


FEED_REGISTRY: tuple[FeedSource, ...] = (
    FeedSource("nber-wp", "NBER", "WORKING_PAPER", "https://back.nber.org/rss/new.xml", "nber"),
    FeedSource("fed-feds", "Federal Reserve Board", "WORKING_PAPER", "https://www.federalreserve.gov/feeds/feds.xml", "fed-paper"),
    FeedSource("fed-ifdp", "Federal Reserve Board", "WORKING_PAPER", "https://www.federalreserve.gov/feeds/ifdp.xml", "fed-paper"),
    FeedSource("fed-press", "Federal Reserve Board", "REGULATORY_RELEASE", "https://www.federalreserve.gov/feeds/press_all.xml"),
    FeedSource("fed-speeches", "Federal Reserve Board", "SPEECH", "https://www.federalreserve.gov/feeds/speeches.xml"),
    FeedSource("sec-press", "SEC", "REGULATORY_RELEASE", "https://www.sec.gov/news/pressreleases.rss"),
    FeedSource("bis-wp", "BIS", "WORKING_PAPER", "https://www.bis.org/doclist/wppubls.rss"),
    FeedSource("bis-cbspeeches", "BIS", "SPEECH", "https://www.bis.org/doclist/cbspeeches.rss"),
    FeedSource("ecb-wp", "ECB", "WORKING_PAPER", "https://www.ecb.europa.eu/rss/wppub.html"),
    FeedSource("ecb-press", "ECB", "REGULATORY_RELEASE", "https://www.ecb.europa.eu/rss/press.html"),
)
FEEDS_BY_ID = {source.source_id: source for source in FEED_REGISTRY}
WORKING_PAPER_FEEDS = tuple(s.source_id for s in FEED_REGISTRY if s.kind == "WORKING_PAPER")


@dataclass(frozen=True)
class FeedRadarFetch:
    source: FeedSource
    query_url: str
    fetched_at: datetime
    items: tuple[DiscoveryItem, ...]
    feed_artifact: ArtifactRef | None


def _default_transport(url: str, headers: dict[str, str], timeout_seconds: float) -> tuple[bytes, str]:
    guarded(url, "research-feed")
    try:
        response = requests.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=False)
    except requests.RequestException as exc:
        raise FeedRadarError("research feed request failed") from exc
    if response.status_code != 200:
        raise FeedRadarError(f"research feed request failed with HTTP {response.status_code}")
    return response.content, response.headers.get("content-type", "application/xml").split(";", 1)[0].strip()


class FeedRadarAdapter:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 20.0,
        min_interval_seconds: float = 1.0,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if "@" not in user_agent and "+http" not in user_agent:
            raise FeedRadarError("user_agent must carry a contact address or URL")
        if min_interval_seconds < 1.0:
            raise FeedRadarError("feed interval must be at least 1 second")
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
        source_id: str,
        max_items: int = 50,
        first_seen: Mapping[str, datetime] | None = None,
        artifact_store: SourceArtifactStore | None = None,
    ) -> FeedRadarFetch:
        source = FEEDS_BY_ID.get(source_id)
        if source is None:
            raise FeedRadarError(f"unregistered feed {source_id!r}")
        if not 1 <= max_items <= 200:
            raise FeedRadarError("max_items must be between 1 and 200")
        if self._last_request_at is not None:
            remaining = self.min_interval_seconds - (self.clock() - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
        body, media_type = self.transport(
            source.url, {"user-agent": self.user_agent, "accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8"}, self.timeout_seconds
        )
        self._last_request_at = self.clock()
        fetched_at = datetime.now(timezone.utc)
        artifact = (
            artifact_store.put(source_uri=source.url, content=body, fetched_at=fetched_at, media_type=media_type or "application/xml")
            if artifact_store is not None and body
            else None
        )
        items = parse_feed(
            body,
            source=source,
            fetched_at=fetched_at,
            feed_artifact_id=artifact.artifact_id if artifact else None,
            first_seen=first_seen,
        )[:max_items]
        return FeedRadarFetch(source=source, query_url=source.url, fetched_at=fetched_at, items=items, feed_artifact=artifact)


def parse_feed(
    body: bytes,
    *,
    source: FeedSource,
    fetched_at: datetime,
    feed_artifact_id: str | None,
    first_seen: Mapping[str, datetime] | None = None,
) -> tuple[DiscoveryItem, ...]:
    if fetched_at.tzinfo is None:
        raise FeedRadarError("fetched_at must be timezone-aware")
    if not body:
        raise FeedRadarError("feed returned an empty body")
    if len(body) > MAX_FEED_BYTES:
        raise FeedRadarError("feed body exceeds the size limit")
    head = body[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in body.upper():
        raise FeedRadarError("feeds with DOCTYPE or entity declarations are refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedRadarError(f"invalid feed XML: {exc}") from exc
    entries = [e for e in root.iter() if _local(e.tag) in {"item", "entry"}]
    if _local(root.tag) not in {"rss", "RDF", "feed"}:
        raise FeedRadarError(f"unsupported feed root {_local(root.tag)!r}")
    seen: dict[str, datetime] = dict(first_seen or {})
    items: list[DiscoveryItem] = []
    ids: set[str] = set()
    for entry in entries:
        raw = _entry_fields(entry)
        title = _clean(raw["title"])
        link = raw["link"].strip()
        if not title or not link:
            raise FeedRadarError(f"{source.source_id} item without title or link")
        authors = raw["authors"]
        summary = raw["description"]
        external_id, canonical = _identity(source, link, raw["guid"])
        if source.parser == "nber" and " -- by " in title:
            title, byline = title.rsplit(" -- by ", 1)
            authors = tuple(a.strip() for a in byline.split(",") if a.strip())
        if source.parser == "fed-paper" and "<br><br>" in summary:
            byline, summary = summary.split("<br><br>", 1)
            authors = tuple(
                a for a in (_clean(part) for part in re.split(r",|\band\b", byline)) if a
            )
        if canonical in ids:
            continue  # feeds occasionally repeat an item; keep the first
        ids.add(canonical)
        dated = raw["published"] or raw["updated"]
        if dated:
            published_at = _parse_date(dated, source.source_id)
            updated_at = _parse_date(raw["updated"], source.source_id) if raw["updated"] else published_at
            precision = "timestamp"
        else:
            published_at = updated_at = seen.get(canonical, fetched_at)
            precision = "first-seen"
        categories = tuple(
            dict.fromkeys(
                [
                    f"publisher:{source.publisher}",
                    f"kind:{source.kind}",
                    f"feed:{source.source_id}",
                    *(f"subject:{_clean(c)}" for c in raw["categories"] if _clean(c)),
                    f"published-precision:{precision}",
                ]
            )
        )
        items.append(
            DiscoveryItem(
                discovery_id=make_discovery_id(provider=source.source_id, canonical_id=canonical, updated_at=updated_at),
                provider=source.source_id,
                external_id=external_id,
                canonical_id=canonical,
                title=title,
                summary=_clean(summary),
                authors=authors,
                categories=categories,
                published_at=published_at,
                updated_at=updated_at,
                discovered_at=fetched_at,
                source_uri=link,
                feed_artifact_id=feed_artifact_id,
            )
        )
    return tuple(items)


def _entry_fields(entry: ET.Element) -> dict:
    fields = {"title": "", "link": "", "guid": "", "description": "", "published": "", "updated": "", "authors": (), "categories": []}
    authors: list[str] = []
    for child in entry:
        name, text = _local(child.tag), (child.text or "").strip()
        namespace = child.tag[: -len(name)] if child.tag.endswith(name) else ""
        if name == "title" and not fields["title"]:
            fields["title"] = text
        elif name == "link":
            fields["link"] = fields["link"] or child.attrib.get("href", "") or text
        elif name in {"guid", "id"}:
            fields["guid"] = text
        elif name in {"description", "summary", "content"} and not fields["description"]:
            fields["description"] = text
        elif name in {"pubDate", "published"} or (name == "date" and namespace == _NS_DC):
            fields["published"] = fields["published"] or text
        elif name == "updated":
            fields["updated"] = text
        elif name == "creator" and text:
            authors.append(_clean(text))
        elif name == "author":
            author_name = child.find(f"{_NS_ATOM}name")
            value = author_name.text if author_name is not None else text
            if value and value.strip():
                authors.append(_clean(value))
        elif name == "category":
            fields["categories"].append(child.attrib.get("term") or text)
    fields["authors"] = tuple(authors)
    return fields


def _identity(source: FeedSource, link: str, guid: str) -> tuple[str, str]:
    if source.parser == "nber":
        match = re.search(r"/papers/(w\d+)", link)
        if not match:
            raise FeedRadarError(f"NBER link without a working-paper number: {link}")
        return match.group(1), f"nber:{match.group(1)}"
    parts = urlsplit(link)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise FeedRadarError(f"{source.source_id} item link is not an http(s) URL")
    path = re.sub(r"/{2,}", "/", parts.path)
    normalized = urlunsplit(("https", parts.netloc.lower(), path, parts.query, ""))
    return (guid or normalized), f"{source.source_id}:{normalized}"


def _parse_date(value: str, source_id: str) -> datetime:
    text = value.strip()
    try:
        if text[:4].isdigit() and "-" in text[:8]:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError) as exc:
        raise FeedRadarError(f"{source_id}: invalid feed date {value!r}") from exc
    if parsed.tzinfo is None:
        raise FeedRadarError(f"{source_id}: feed date without a timezone {value!r}")
    return parsed.astimezone(timezone.utc)


def _clean(value: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
