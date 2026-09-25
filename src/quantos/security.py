"""Stage 16.2 — security controls that sit outside research and strategy code.

* ``KillSwitch`` — an operator-controlled stop for all outbound activity,
  engaged by a file or an environment variable. It is checked by the egress
  guard itself, so no research, strategy or adapter code can bypass it by
  forgetting to ask.
* ``SecretProvider`` / ``Secret`` — credentials come from the environment or
  a permission-checked secrets directory, never from source; secret values
  never appear in ``repr``/``str`` and are registered with the process-wide
  ``REDACTOR`` so logs and errors scrub them.
* ``EgressGuard`` — every outbound request must be HTTPS to an allowlisted
  host with the kill switch released; denials are errors, not warnings.
* ``AuditLog`` — an append-only, SHA-256 hash-chained DuckDB ledger for
  security-relevant events; ``verify()`` detects edits, deletions and
  reordering.

None of these grants any authority. They only restrict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import duckdb


class SecurityControlError(PermissionError):
    """Base class for security-control refusals."""


class KillSwitchEngaged(SecurityControlError):
    pass


class NetworkDenied(SecurityControlError):
    pass


class SecretUnavailable(SecurityControlError):
    pass


# --------------------------------------------------------------- redaction


class SecretRedactor:
    """Scrubs registered secret values and credential-like query parameters."""

    SENSITIVE_PARAMETERS = frozenset(
        {"api_key", "apikey", "key", "token", "access_token", "secret", "password", "signature"}
    )

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> None:
        if len(value) >= 4:
            with self._lock:
                self._values.add(value)

    def redact(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, "***")
        return re.sub(
            r"(?i)\b(" + "|".join(sorted(self.SENSITIVE_PARAMETERS)) + r")=([^&\s\"']+)",
            lambda m: f"{m.group(1)}=***",
            text,
        )

    def redact_url(self, url: str) -> str:
        parts = urlsplit(url)
        query = urlencode(
            [
                (k, "***" if k.lower() in self.SENSITIVE_PARAMETERS else v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return self.redact(urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")))


REDACTOR = SecretRedactor()


# ------------------------------------------------------------------ secrets


class Secret:
    """A credential whose value is only available through ``reveal()``."""

    __slots__ = ("name", "_value")

    def __init__(self, name: str, value: str) -> None:
        if not value:
            raise SecretUnavailable(f"secret {name} is empty")
        self.name = name
        self._value = value
        REDACTOR.register(value)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret(name={self.name!r}, value='***')"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("secrets cannot be pickled")


class SecretProvider:
    """Resolves named secrets from ``QUANTOS_SECRET_<NAME>`` or a directory.

    A secrets directory (``QUANTOS_SECRETS_DIR``) must not be readable by
    group or others on POSIX systems; a world-readable secret file is refused.
    """

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        secrets_dir: str | Path | None = None,
        audit: "AuditLog | None" = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        configured = secrets_dir or self._environ.get("QUANTOS_SECRETS_DIR")
        self._dir = Path(configured) if configured else None
        self._audit = audit

    def get(self, name: str) -> Secret:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name):
            raise SecretUnavailable("secret names are upper-case identifiers")
        value = self._environ.get(f"QUANTOS_SECRET_{name}")
        source = "environment"
        if value is None and self._dir is not None:
            path = self._dir / name
            if path.is_file():
                mode = path.stat().st_mode
                if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
                    raise SecretUnavailable(
                        f"secret file for {name} is accessible to group/others"
                    )
                value = path.read_text().strip()
                source = "secrets_dir"
        if not value:
            raise SecretUnavailable(f"secret {name} is not configured")
        if self._audit is not None:
            self._audit.append(
                actor="secret-provider",
                action="SECRET_RESOLVED",
                subject=name,
                details={"source": source},
            )
        return Secret(name, value)


# -------------------------------------------------------------- kill switch


@dataclass(frozen=True)
class KillSwitchStatus:
    engaged: bool
    reason: str


class KillSwitch:
    """Operator stop for outbound activity; independent of strategy code."""

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self.path = Path(
            path or self._environ.get("QUANTOS_KILL_SWITCH_FILE", "data/KILL_SWITCH")
        )

    def status(self) -> KillSwitchStatus:
        if self._environ.get("QUANTOS_KILL_SWITCH", "").strip() == "1":
            return KillSwitchStatus(True, "QUANTOS_KILL_SWITCH=1")
        if self.path.exists():
            reason = self.path.read_text().strip() or "kill switch file present"
            return KillSwitchStatus(True, reason)
        return KillSwitchStatus(False, "released")

    def require_released(self, operation: str) -> None:
        status = self.status()
        if status.engaged:
            raise KillSwitchEngaged(f"{operation} refused: kill switch engaged ({status.reason})")

    def engage(self, *, reason: str, actor: str, audit: "AuditLog | None" = None) -> None:
        if not reason.strip() or not actor.strip():
            raise ValueError("engaging the kill switch requires an actor and a reason")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{reason.strip()} (by {actor.strip()} at {datetime.now(timezone.utc).isoformat()})\n")
        if audit is not None:
            audit.append(actor=actor, action="KILL_SWITCH_ENGAGED", subject=str(self.path), details={"reason": reason})

    def release(self, *, actor: str, audit: "AuditLog | None" = None) -> None:
        if not actor.strip():
            raise ValueError("releasing the kill switch requires an actor")
        if self.path.exists():
            self.path.unlink()
        if audit is not None:
            audit.append(actor=actor, action="KILL_SWITCH_RELEASED", subject=str(self.path), details={})


# ------------------------------------------------------------------- egress

DEFAULT_EGRESS_ALLOWLIST = frozenset(
    {
        "export.arxiv.org",
        "api.crossref.org",
        "data.sec.gov",
        "www.sec.gov",
        "api.stlouisfed.org",
        "api.tiingo.com",
        "api.polygon.io",
        "www.nber.org",
        "back.nber.org",
        "home.treasury.gov",
        "data-api.ecb.europa.eu",
        "fred.stlouisfed.org",
        "registry.npmjs.org",
        "www.nasdaqtrader.com",
        "mba.tuck.dartmouth.edu",
        "www.federalreserve.gov",
        "www.bis.org",
        "www.ecb.europa.eu",
    }
)


@dataclass(frozen=True)
class EgressPolicy:
    allowed_hosts: frozenset[str]

    @classmethod
    def default(cls, environ: dict[str, str] | None = None) -> "EgressPolicy":
        env = environ if environ is not None else os.environ
        extra = {
            host.strip().lower()
            for host in env.get("QUANTOS_EGRESS_EXTRA_HOSTS", "").split(",")
            if host.strip()
        }
        return cls(frozenset(DEFAULT_EGRESS_ALLOWLIST | extra))


class EgressGuard:
    """The single gate every outbound request passes through."""

    def __init__(
        self,
        *,
        policy: EgressPolicy | None = None,
        kill_switch: KillSwitch | None = None,
        audit: "AuditLog | None" = None,
    ) -> None:
        self.policy = policy or EgressPolicy.default()
        self.kill_switch = kill_switch or KillSwitch()
        self.audit = audit

    def check(self, url: str, *, purpose: str) -> None:
        from .observability import emit

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        reason = None
        status = self.kill_switch.status()
        if status.engaged:
            reason = f"kill switch engaged ({status.reason})"
        elif parts.scheme != "https":
            reason = "only HTTPS egress is permitted"
        elif host not in self.policy.allowed_hosts:
            reason = f"host {host or '?'} is not on the egress allowlist"
        if reason is not None:
            emit("security", "EGRESS_DENIED", level="WARNING", url=REDACTOR.redact_url(url), purpose=purpose, reason=reason)
            if self.audit is not None:
                self.audit.append(
                    actor="egress-guard",
                    action="EGRESS_DENIED",
                    subject=host or "?",
                    details={"purpose": purpose, "reason": reason},
                )
            if status.engaged:
                raise KillSwitchEngaged(f"{purpose} refused: {reason}")
            raise NetworkDenied(f"{purpose} refused: {reason}")
        emit("security", "EGRESS_ALLOWED", url=REDACTOR.redact_url(url), purpose=purpose)

    def get(self, url: str, *, purpose: str, headers: dict[str, str] | None = None, timeout: float = 20.0):
        """HTTPS GET through the guard (requests-based)."""

        import requests

        self.check(url, purpose=purpose)
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        return response


_GUARD: EgressGuard | None = None


def default_egress_guard() -> EgressGuard:
    """Process-wide guard; policy and kill switch are re-read on each check."""

    global _GUARD
    if _GUARD is None:
        _GUARD = EgressGuard()
    return _GUARD


def guarded(url: str, purpose: str) -> None:
    EgressGuard(policy=EgressPolicy.default(), kill_switch=KillSwitch()).check(url, purpose=purpose)


# ---------------------------------------------------------------- audit log

_GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    recorded_at: datetime
    actor: str
    action: str
    subject: str
    details: dict
    previous_hash: str
    record_hash: str


@dataclass(frozen=True)
class AuditVerification:
    valid: bool
    records: int
    first_bad_sequence: int | None
    reason: str


class AuditLog:
    """Append-only, hash-chained audit ledger."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._lock = threading.Lock()
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                sequence BIGINT PRIMARY KEY,
                recorded_at VARCHAR NOT NULL,
                actor VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                subject VARCHAR NOT NULL,
                details_json VARCHAR NOT NULL,
                previous_hash VARCHAR NOT NULL,
                record_hash VARCHAR NOT NULL
            )
            """
        )

    def append(self, *, actor: str, action: str, subject: str, details: dict) -> AuditRecord:
        if not actor.strip() or not action.strip():
            raise ValueError("audit records need an actor and an action")
        details_json = REDACTOR.redact(json.dumps(details, sort_keys=True, separators=(",", ":")))
        with self._lock:
            row = self._con.execute(
                "SELECT sequence, record_hash FROM audit_log ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence, previous = (row[0] + 1, row[1]) if row else (1, _GENESIS)
            recorded_at = datetime.now(timezone.utc).isoformat()
            record_hash = _audit_hash(sequence, recorded_at, actor, action, subject, details_json, previous)
            self._con.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [sequence, recorded_at, actor, action, subject, details_json, previous, record_hash],
            )
        return AuditRecord(
            sequence, datetime.fromisoformat(recorded_at), actor, action, subject,
            json.loads(details_json), previous, record_hash,
        )

    def records(self) -> tuple[AuditRecord, ...]:
        rows = self._con.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        return tuple(
            AuditRecord(r[0], datetime.fromisoformat(r[1]), r[2], r[3], r[4], json.loads(r[5]), r[6], r[7])
            for r in rows
        )

    def verify(self) -> AuditVerification:
        rows = self._con.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        previous = _GENESIS
        for index, (sequence, recorded_at, actor, action, subject, details_json, prev, record_hash) in enumerate(rows, start=1):
            if sequence != index:
                return AuditVerification(False, len(rows), sequence, "sequence gap or reordering")
            if prev != previous:
                return AuditVerification(False, len(rows), sequence, "broken hash chain")
            if _audit_hash(sequence, recorded_at, actor, action, subject, details_json, prev) != record_hash:
                return AuditVerification(False, len(rows), sequence, "record content altered")
            previous = record_hash
        return AuditVerification(True, len(rows), None, "hash chain intact")

    def close(self) -> None:
        self._con.close()


def _audit_hash(sequence, recorded_at, actor, action, subject, details_json, previous) -> str:
    material = json.dumps(
        [sequence, recorded_at, actor, action, subject, details_json, previous],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
