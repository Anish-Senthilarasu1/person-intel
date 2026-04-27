import hashlib
import json
import time
from pathlib import Path
from typing import Any


class Cache:
    """Simple file-based cache using stdlib only — no diskcache dependency."""

    def __init__(self, cache_dir: Path, ttl_hours: int = 24):
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._dir = cache_dir
        self._ttl = ttl_hours * 3600

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, url: str) -> Any | None:
        p = self._path(self._key(url))
        if not p.exists():
            return None
        try:
            entry = json.loads(p.read_text())
            if time.time() > entry["expires"]:
                p.unlink(missing_ok=True)
                return None
            return entry["value"]
        except Exception:
            return None

    def set(self, url: str, value: Any) -> None:
        p = self._path(self._key(url))
        try:
            p.write_text(json.dumps({"value": value, "expires": time.time() + self._ttl}))
        except Exception:
            pass

    def close(self) -> None:
        pass  # nothing to close
