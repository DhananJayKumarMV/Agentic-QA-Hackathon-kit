"""Evidence directories (runs/<id>/evidence/<scenario>/) and redaction of secrets in saved artifacts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..contracts import Evidence, NetworkEvent

REDACTED = "[REDACTED]"
_SECRET_KEY_RE = re.compile(
    r"(cookie|token|authorization|auth|session|secret|password|passwd|api[_-]?key|csrf|xsrf|bearer|jwt)",
    re.I,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120] or "x"


def redact(obj: Any) -> Any:
    """Recursively replace secret-looking keys' values and bearer/JWT strings."""
    if isinstance(obj, dict):
        return {k: (REDACTED if isinstance(k, str) and _SECRET_KEY_RE.search(k) and obj[k] not in (None, "")
                    else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _JWT_RE.sub(REDACTED, _BEARER_RE.sub("Bearer " + REDACTED, obj))
    return obj


def redact_url(url: str) -> str:
    p = urlparse(url)
    if not p.query:
        return url
    q = [(k, REDACTED if _SECRET_KEY_RE.search(k) else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunparse(p._replace(query=urlencode(q, safe="[]")))


def redact_event(ev: NetworkEvent) -> dict[str, Any]:
    d = ev.model_dump()
    d["url"] = redact_url(d["url"])
    d["req_body"] = redact(d["req_body"])
    d["resp_body"] = redact(d["resp_body"])
    return d


class EvidenceStore:
    """Owns <evidence_root>/<scenario>/ for one scenario attempt."""

    def __init__(self, evidence_root: Path, scenario_id: str, attempt: int = 1) -> None:
        self.root = Path(evidence_root)
        self.scenario_id = scenario_id
        self.attempt = attempt
        self.dir = self.root / safe_name(scenario_id)
        self.dir.mkdir(parents=True, exist_ok=True)

    def name(self, label: str) -> str:
        """Screenshot/file stem unique to this scenario attempt (flat: the browser owns its own dir)."""
        return safe_name(f"{self.scenario_id}__a{self.attempt}__{label}")

    def path(self, label: str, suffix: str) -> Path:
        return self.dir / f"a{self.attempt}_{safe_name(label)}{suffix}"

    def save_json(self, label: str, data: Any, *, kind: str = "dom", caption: str = "") -> Evidence:
        p = self.path(label, ".json")
        p.write_text(json.dumps(redact(data), indent=2, default=str))
        return Evidence(kind=kind, path=str(p), caption=caption or label)  # type: ignore[arg-type]

    def save_network(self, events: list[NetworkEvent]) -> Evidence:
        p = self.path("network", ".json")
        p.write_text(json.dumps([redact_event(e) for e in events], indent=2, default=str))
        return Evidence(kind="network", path=str(p), caption=f"{len(events)} API calls during the scenario")

    def save_text(self, label: str, text: str, *, kind: str = "aria", caption: str = "") -> Evidence:
        p = self.path(label, ".txt")
        p.write_text(text)
        return Evidence(kind=kind, path=str(p), caption=caption or label)  # type: ignore[arg-type]
