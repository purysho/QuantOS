from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable
from urllib.request import Request, urlopen

from quantos.artifacts import ArtifactRef, SourceArtifactStore
from quantos.lineage import LineageStore
from quantos.models import Event


class SECFilingArtifactError(ValueError):
    pass


Transport = Callable[
    [str, dict[str, str], float, int],
    tuple[bytes, str],
]


_CIK = re.compile(r"^CIK:(\d{10})$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[bytes, str]:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise SECFilingArtifactError(
                f"SEC document exceeds configured limit of {max_bytes} bytes"
            )
        media_type = response.headers.get_content_type() or "application/octet-stream"
    return content, media_type


class SECFilingArtifactFetcher:
    """Capture an already-discovered SEC filing document as immutable evidence."""

    archive_base = "https://www.sec.gov/Archives/edgar/data"

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 15.0,
        max_bytes: int = 50 * 1024 * 1024,
        transport: Transport | None = None,
    ) -> None:
        if "@" not in user_agent:
            raise SECFilingArtifactError(
                "SEC user_agent must identify the application and include a contact email"
            )
        if timeout_seconds <= 0:
            raise SECFilingArtifactError("timeout_seconds must be positive")
        if max_bytes <= 0:
            raise SECFilingArtifactError("max_bytes must be positive")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.transport = transport or _default_transport

    @classmethod
    def primary_document_url(cls, event: Event) -> str:
        if not event.event_type.startswith("sec.filing."):
            raise SECFilingArtifactError("event is not an SEC filing event")

        cik_match = _CIK.fullmatch(event.entity_id)
        if cik_match is None:
            raise SECFilingArtifactError("SEC event has invalid CIK entity_id")
        cik = str(int(cik_match.group(1)))

        accession = event.payload.get("accessionNumber")
        if not isinstance(accession, str) or _ACCESSION.fullmatch(accession) is None:
            raise SECFilingArtifactError("SEC event has invalid accession number")

        document = event.payload.get("primaryDocument")
        if (
            not isinstance(document, str)
            or _DOCUMENT.fullmatch(document) is None
            or document in {".", ".."}
            or "/" in document
            or "\\" in document
            or "?" in document
            or "#" in document
        ):
            raise SECFilingArtifactError("SEC event has unsafe primaryDocument")

        accession_path = accession.replace("-", "")
        return f"{cls.archive_base}/{cik}/{accession_path}/{document}"

    def capture_primary_document(
        self,
        *,
        event: Event,
        artifact_store: SourceArtifactStore,
        lineage_store: LineageStore,
    ) -> ArtifactRef:
        url = self.primary_document_url(event)
        content, media_type = self.transport(
            url,
            {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
            self.timeout_seconds,
            self.max_bytes,
        )
        if not isinstance(content, bytes):
            raise SECFilingArtifactError("SEC transport returned non-bytes content")
        if not content:
            raise SECFilingArtifactError("SEC transport returned an empty document")

        fetched_at = datetime.now(timezone.utc)
        ref = artifact_store.put(
            source_uri=url,
            content=content,
            fetched_at=fetched_at,
            media_type=media_type or "application/octet-stream",
        )
        lineage_store.link(
            event_id=event.event_id,
            role="sec.primary_document",
            artifact_id=ref.artifact_id,
            linked_at=fetched_at,
        )
        return ref
