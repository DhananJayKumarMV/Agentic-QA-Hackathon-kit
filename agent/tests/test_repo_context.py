from pathlib import Path

import pytest

from qa_agent.context.repo import discover_routes, resolve_repo


def test_discover_routes_finds_concrete_client_routes(tmp_path: Path):
    src = tmp_path / "web" / "src"
    src.mkdir(parents=True)
    (src / "App.tsx").write_text(
        '<Route path="/" element={<A/>} />\n<Route path="/orders" element={<B/>} />\n'
        "<Route path='/orders/:id' element={<C/>} />\n<Route path=\"*\" element={<D/>} />\n"
    )
    (src / "router.js").write_text("const routes = [{ path: '/settings/' }, { path: \"/help\" }]")
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "x.js").write_text('{ path: "/ignored" }')
    assert discover_routes(tmp_path) == ["/", "/help", "/orders", "/settings"]


def test_resolve_repo_accepts_dirs_and_rejects_other_strings(tmp_path: Path):
    assert resolve_repo(str(tmp_path), tmp_path / "cache") == tmp_path
    with pytest.raises(ValueError):
        resolve_repo("not-a-dir-or-url", tmp_path / "cache")
