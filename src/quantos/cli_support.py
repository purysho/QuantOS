"""Shared CLI behavior: friendly, categorized errors instead of tracebacks."""

from __future__ import annotations

import os
import sys
import traceback
from typing import Callable

from .observability import classify_exception


def friendly(entry: Callable[[], int], program: str) -> int:
    """Runs a CLI entry point.

    Expected failures (validation, refusals, network denial, missing
    secrets, kill switch) print one line with their category and exit 2.
    ``QUANTOS_DEBUG=1`` shows the full traceback.
    """

    try:
        return entry()
    except KeyboardInterrupt:
        print(f"\n{program}: interrupted", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - this is the top-level boundary
        if os.environ.get("QUANTOS_DEBUG") == "1":
            traceback.print_exc()
        category = classify_exception(exc).value.replace("_", " ").lower()
        print(f"{program}: {exc}", file=sys.stderr)
        print(f"({category}; set QUANTOS_DEBUG=1 for details, `quantos doctor` to check the installation)",
              file=sys.stderr)
        return 2
