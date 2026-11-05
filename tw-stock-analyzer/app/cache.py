"""簡單的磁碟快取：避免重複打外部 API（FinMind 免費版有每小時次數限制）。"""
from __future__ import annotations

import hashlib
import pickle
import time
from pathlib import Path
from typing import Any, Callable

from .config import settings


def _path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return settings.cache_dir / f"{digest}.pkl"


def get(key: str, ttl: int | None = None) -> Any | None:
    path = _path(key)
    if not path.exists():
        return None
    ttl = settings.cache_ttl if ttl is None else ttl
    if time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def put(key: str, value: Any) -> None:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(key).with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(value, fh)
    tmp.replace(_path(key))


def cached(key: str, fn: Callable[[], Any], ttl: int | None = None) -> Any:
    hit = get(key, ttl)
    if hit is not None:
        return hit
    value = fn()
    if value is not None:
        put(key, value)
    return value
