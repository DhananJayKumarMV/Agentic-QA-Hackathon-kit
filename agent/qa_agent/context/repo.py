"""Optional target source as context, never as an answer key.

The only thing taken from the repository is the list of client-side routes, which the Explorer
adds to its crawl so pages without a visible link are still modelled. Expected behaviour still
comes only from the PRD, and verdicts only from the live site.
"""
import hashlib
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".vue", ".svelte"}
SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".next", "coverage", ".venv", "__pycache__"}
# path="/x" or path: "/x" (React Router, Vue Router), and <Route path='/x'>.
ROUTE_RX = re.compile(r"""\bpath\s*[:=]\s*\{?\s*["'](/[A-Za-z0-9_\-/:.*]*)["']""")
MAX_FILES = 2_000


def resolve_repo(repo: str, cache_dir: Path) -> Path:
    """Local directory as-is; a git URL is shallow-cloned once into the cache directory."""
    path = Path(repo).expanduser()
    if path.is_dir():
        return path
    if not re.match(r"^(https?://|git@)", repo):
        raise ValueError(f"--repo must be an existing directory or a git URL: {repo!r}")
    dest = cache_dir / "repos" / hashlib.sha256(repo.encode()).hexdigest()[:16]
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--quiet", repo, str(dest)], check=True, timeout=180)
    return dest


def discover_routes(root: Path) -> list[str]:
    """Concrete client-side route paths declared in frontend source (parameterised ones skipped)."""
    routes: set[str] = set()
    seen = 0
    for f in root.rglob("*"):
        if seen >= MAX_FILES:
            break
        if f.suffix not in SOURCE_SUFFIXES or any(part in SKIP_DIRS for part in f.parts) or not f.is_file():
            continue
        seen += 1
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in ROUTE_RX.finditer(text):
            route = m.group(1).rstrip("/") or "/"
            if ":" in route or "*" in route:
                continue  # needs a live id; the Explorer reaches those through links
            routes.add(route)
    return sorted(routes)
