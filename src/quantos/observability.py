"""Stage 16.1 — operational observability.

* Run and span IDs propagate through ``contextvars``, so every log line and
  metric emitted inside ``span(...)`` carries its run, span and parent.
* Structured events are JSON lines (one object per line) with secrets
  scrubbed by the security redactor before anything is written.
* An error taxonomy classifies failures (fail-closed validation, identity
  mismatch, scope refusal, external engine, provider, network denial,
  kill switch, internal) so operations can see *why* things stop.
* An in-process metrics registry (counters and duration summaries) and an
  optional DuckDB sink make runs inspectable afterwards (``quantos
  ops-report``).

Observability never changes results: it records, it does not decide.
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, TextIO

import duckdb


class ErrorCategory(str, Enum):
    FAIL_CLOSED_VALIDATION = "FAIL_CLOSED_VALIDATION"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SCOPE_REFUSED = "SCOPE_REFUSED"
    EXTERNAL_ENGINE_FAILURE = "EXTERNAL_ENGINE_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    NETWORK_DENIED = "NETWORK_DENIED"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def classify_exception(exc: BaseException) -> ErrorCategory:
    from .security import KillSwitchEngaged, NetworkDenied, SecretUnavailable

    if isinstance(exc, KillSwitchEngaged):
        return ErrorCategory.KILL_SWITCH_ENGAGED
    if isinstance(exc, NetworkDenied):
        return ErrorCategory.NETWORK_DENIED
    if isinstance(exc, SecretUnavailable):
        return ErrorCategory.SECRET_UNAVAILABLE
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    if "identity mismatch" in message or "identity conflict" in message:
        return ErrorCategory.IDENTITY_MISMATCH
    if any(word in message for word in ("outside the", "refuse", "not supported", "unsupported", "equivalence contract", "overlap")):
        return ErrorCategory.SCOPE_REFUSED
    if any(word in message for word in ("ore ", "nautilus", "quantlib", "worker")):
        return ErrorCategory.EXTERNAL_ENGINE_FAILURE
    if "radar" in name or "adapter" in name or "provider" in name or "http" in message:
        return ErrorCategory.PROVIDER_FAILURE
    if isinstance(exc, ValueError):
        return ErrorCategory.FAIL_CLOSED_VALIDATION
    return ErrorCategory.INTERNAL_ERROR


@dataclass(frozen=True)
class SpanRecord:
    run_id: str
    span_id: str
    parent_span_id: str | None
    component: str
    operation: str
    started_at: datetime
    duration_ms: float
    status: str
    error_category: str | None
    attributes: dict = field(default_factory=dict)


_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("quantos_run_id", default=None)
_SPAN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("quantos_span_id", default=None)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], int] = {}
        self._durations: dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe_duration(self, name: str, milliseconds: float) -> None:
        with self._lock:
            self._durations.setdefault(name, []).append(milliseconds)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = {
                name + ("{" + ",".join(f"{k}={v}" for k, v in labels) + "}" if labels else ""): value
                for (name, labels), value in sorted(self._counters.items())
            }
            durations = {
                name: {
                    "count": len(values),
                    "max_ms": max(values),
                    "mean_ms": sum(values) / len(values),
                }
                for name, values in sorted(self._durations.items())
            }
        return {"counters": counters, "durations": durations}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._durations.clear()


METRICS = MetricsRegistry()


class EventSink:
    """Writes redacted JSON-line events to a stream, a file, and/or DuckDB."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stream: TextIO | None = None
        self.path: Path | None = None
        self.store: "ObservabilityStore | None" = None
        self.minimum_level = "INFO"
        configured = os.environ.get("QUANTOS_EVENT_LOG")
        if configured:
            self.path = Path(configured)

    _LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

    def write(self, event: dict) -> None:
        from .security import REDACTOR

        if self._LEVELS.get(event.get("level", "INFO"), 20) < self._LEVELS[self.minimum_level]:
            return
        line = REDACTOR.redact(json.dumps(event, sort_keys=True, default=str))
        with self._lock:
            if self.stream is not None:
                self.stream.write(line + "\n")
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")


SINK = EventSink()


def current_run_id() -> str | None:
    return _RUN_ID.get()


def emit(component: str, event: str, *, level: str = "INFO", **fields) -> dict:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "component": component,
        "event": event,
        "run_id": _RUN_ID.get(),
        "span_id": _SPAN_ID.get(),
        **fields,
    }
    METRICS.increment("events", component=component, event=event)
    SINK.write(record)
    return record


@contextmanager
def span(component: str, operation: str, **attributes) -> Iterator[str]:
    """Traced unit of work; creates a run ID if none is active."""

    run_token = None
    if _RUN_ID.get() is None:
        run_token = _RUN_ID.set("run:" + uuid.uuid4().hex)
    parent = _SPAN_ID.get()
    span_id = "span:" + uuid.uuid4().hex[:16]
    span_token = _SPAN_ID.set(span_id)
    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    status, category = "OK", None
    emit(component, f"{operation}.start", parent_span_id=parent, **attributes)
    try:
        yield span_id
    except BaseException as exc:
        status, category = "ERROR", classify_exception(exc).value
        METRICS.increment("errors", component=component, category=category)
        emit(component, f"{operation}.error", level="ERROR", error_category=category,
             error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        duration = (time.perf_counter() - clock) * 1000
        METRICS.observe_duration(f"{component}.{operation}", duration)
        emit(component, f"{operation}.end", status=status, duration_ms=round(duration, 3))
        record = SpanRecord(
            run_id=_RUN_ID.get() or "",
            span_id=span_id,
            parent_span_id=parent,
            component=component,
            operation=operation,
            started_at=started,
            duration_ms=duration,
            status=status,
            error_category=category,
            attributes={k: str(v) for k, v in attributes.items()},
        )
        if SINK.store is not None:
            SINK.store.add_span(record)
        _SPAN_ID.reset(span_token)
        if run_token is not None:
            _RUN_ID.reset(run_token)


class ObservabilityStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._lock = threading.Lock()
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS spans (
                span_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                parent_span_id VARCHAR,
                component VARCHAR NOT NULL,
                operation VARCHAR NOT NULL,
                started_at VARCHAR NOT NULL,
                duration_ms DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                error_category VARCHAR,
                attributes_json VARCHAR NOT NULL
            )
            """
        )

    def add_span(self, record: SpanRecord) -> None:
        from .security import REDACTOR

        with self._lock:
            self._con.execute(
                "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    record.span_id, record.run_id, record.parent_span_id, record.component,
                    record.operation, record.started_at.isoformat(), record.duration_ms,
                    record.status, record.error_category,
                    REDACTOR.redact(json.dumps(record.attributes, sort_keys=True)),
                ],
            )

    def report(self) -> dict[str, object]:
        by_component = self._con.execute(
            """
            SELECT component, operation, count(*), sum(CASE WHEN status='ERROR' THEN 1 ELSE 0 END),
                   max(duration_ms)
            FROM spans GROUP BY component, operation ORDER BY component, operation
            """
        ).fetchall()
        errors = self._con.execute(
            "SELECT error_category, count(*) FROM spans WHERE status='ERROR' GROUP BY 1 ORDER BY 1"
        ).fetchall()
        runs = self._con.execute("SELECT count(DISTINCT run_id) FROM spans").fetchone()[0]
        return {
            "runs": runs,
            "operations": [
                {"component": c, "operation": o, "count": n, "errors": e, "max_duration_ms": round(d, 3)}
                for c, o, n, e, d in by_component
            ],
            "errors_by_category": {category: count for category, count in errors},
        }

    def close(self) -> None:
        self._con.close()


def ops_report(*, db: str) -> int:
    store = ObservabilityStore(db)
    try:
        print(json.dumps(store.report(), indent=2, sort_keys=True))
    finally:
        store.close()
    return 0


def configure_stderr_events() -> None:
    SINK.stream = sys.stderr
