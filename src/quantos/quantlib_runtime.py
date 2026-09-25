from __future__ import annotations

import threading


# QuantLib exposes process-global mutable settings, including evaluationDate.
# Every QuantOS adapter that mutates those settings must serialize through
# this single lock and restore prior state in a finally block.
QUANTLIB_LOCK = threading.RLock()
