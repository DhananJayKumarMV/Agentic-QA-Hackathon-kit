"""Content-addressed disk cache for LLM outputs (requirements, plans, page labels).

The key is a hash of everything that affects the output, so a rerun against the same
PRD and site reuses earlier results instead of paying for them again.
"""
import hashlib
import json
from pathlib import Path
from typing import Any


def stable_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance. Also keeps prompt caching stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def cache_key(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(stable_json(part).encode())
        h.update(b"\x00")
    return h.hexdigest()


class ArtifactCache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def put(self, namespace: str, key: str, value: Any, meta: dict[str, Any] | None = None) -> None:
        """Store `value`; `meta` (e.g. the original call's usage and cost) goes to a sidecar file."""
        if not self.enabled:
            return
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stable_json(value))
        if meta is not None:
            path.with_suffix(".meta.json").write_text(stable_json(meta))

    def get_meta(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Sidecar metadata written by `put`, or None (also for entries cached before sidecars existed)."""
        if not self.enabled:
            return None
        path = self._path(namespace, key).with_suffix(".meta.json")
        if not path.exists():
            return None
        try:
            meta = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        return meta if isinstance(meta, dict) else None
