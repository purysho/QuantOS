"""External event adapters.

Adapters may fetch untrusted external data, but they only emit typed Event
objects. They have no access to risk limits, broker credentials, or execution.
"""

from .sec import SECSubmissionsAdapter

__all__ = ["SECSubmissionsAdapter"]
