"""Idempotent on-disk cache. sha256(url) for scrapes, sha256(query) for Tavily.
Re-runs hit cache — this is also the live-demo idempotency insurance: rehearsal
evidence is captured here and reused on stage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

import orjson


CACHE_DIR = Path(__file__).resolve().parent / "caches"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _key(namespace: str, raw: str) -> Path:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    sub = CACHE_DIR / namespace
    sub.mkdir(parents=True, exist_ok=True)
    return sub / f"{digest}.json"


def get(namespace: str, raw: str) -> Optional[Any]:
    path = _key(namespace, raw)
    if not path.exists():
        return None
    try:
        return orjson.loads(path.read_bytes())
    except Exception:
        return None


def set(namespace: str, raw: str, value: Any) -> None:  # noqa: A001 - intentional API
    path = _key(namespace, raw)
    path.write_bytes(orjson.dumps(value))
