import hashlib
import json
from typing import Any


def fingerprint(*parts: Any) -> str:
    """sha256 hex digest (64 chars — matches request_fingerprint's column width).

    Callers must pass already-normalized values (e.g. UTC-normalized datetimes via
    Pydantic's mode="json") — this only canonicalizes key order/whitespace, not semantic
    equivalence. Include every input that defines "the same request", not just the body
    — e.g. a path parameter like a parent resource ID.
    """
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
