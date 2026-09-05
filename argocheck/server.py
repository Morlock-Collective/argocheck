"""FastAPI web server for argocheck."""
from __future__ import annotations

import asyncio
import tempfile
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from argocheck import recents as _recents
from argocheck.helm import HelmError, check_helm
from argocheck.models import AppNode, HelmSource
from argocheck.parser import ParseError, load_yaml_file, parse_application
from argocheck.valuetree import ValueTreeError, build_leaf_node, parse_leaves
from argocheck.walker import walk

_STATIC = Path(__file__).parent / "static"
_EXECUTOR = ThreadPoolExecutor(max_workers=2)

app = FastAPI(title="argocheck", docs_url=None, redoc_url=None)


# ── Serialisation ─────────────────────────────────────────────────────────────

def _ser_error(e: Exception | None) -> dict[str, Any] | None:
    if e is None:
        return None
    out: dict[str, Any] = {"message": str(e), "type": type(e).__name__}
    if hasattr(e, "cmd") and e.cmd:
        out["cmd"] = " ".join(str(c) for c in e.cmd)
    if hasattr(e, "stderr") and e.stderr:
        out["stderr"] = e.stderr
    return out


def _ser_source(s: HelmSource) -> dict[str, Any]:
    return {
        "repoURL": s.repo_url,
        "ref": s.ref,
        "chart": s.chart,
        "path": s.path,
        "targetRevision": s.target_revision,
        "releaseName": s.release_name,
        "isRefOnly": s.is_ref_only,
        "valueFiles": s.value_files,
        "parameters": [{"name": p.name, "value": p.value} for p in s.parameters],
    }


def _ser_node(node: AppNode, tmp_dir: Path) -> dict[str, Any]:
    # chart_dir may point into tmp_dir (e.g. a git/local-git scratch clone), which
    # is deleted once this request finishes — never surface a path the client
    # can't actually use.
    chart_dir = node.chart_dir
    durable_chart_dir = (
        chart_dir is not None and tmp_dir.resolve() not in chart_dir.resolve().parents
    )
    return {
        "name": node.name,
        "namespace": node.namespace,
        "sources": [_ser_source(s) for s in node.sources],
        "isMultiSource": node.is_multi_source,
        "manifests": node.manifests,
        "appManifest": node.app_manifest,
        "chartDir": str(chart_dir) if durable_chart_dir else None,
        "error": _ser_error(node.error),
        "children": [_ser_node(c, tmp_dir) for c in node.children],
    }


# ── Render worker (runs in thread pool) ──────────────────────────────────────

class RenderRequest(BaseModel):
    path: str
    argocd_env: bool = False
    max_depth: int = 10
    values_override: str | None = None
    # Optional value-tree (environment map) add-on, fanning `path`'s root out
    # across a nested value map instead of rendering it once. At most one of
    # these two should be set (the client's radio button enforces this).
    env_map_path: str | None = None
    env_map_yaml: str | None = None
    # None => just enumerate leaves, don't render anything yet (cheap, no
    # helm calls). A list => render exactly those leaves (by their
    # "/"-joined display path).
    selected_leaves: list[str] | None = None


def _chart_root_node(chart_dir: Path, values_override: str | None) -> AppNode:
    """Build a pseudo-root AppNode for a bare Helm chart directory."""
    name = chart_dir.name
    try:
        chart_meta = yaml.safe_load((chart_dir / "Chart.yaml").read_text()) or {}
        name = chart_meta.get("name") or name
    except (OSError, yaml.YAMLError):
        pass

    source = HelmSource(repo_url=str(chart_dir), values=values_override or None)
    return AppNode(name=name, namespace="default", sources=[source])


def _load_env_map_doc(req: RenderRequest) -> tuple[dict[str, Any] | None, str | None]:
    """Load the optional env-map doc from either field. Returns (doc, error)."""
    if req.env_map_path and req.env_map_yaml:
        return None, "Provide either an env-map file path or pasted YAML, not both."
    if req.env_map_path:
        try:
            doc = load_yaml_file(Path(req.env_map_path).expanduser())
        except ParseError as e:
            return None, str(e)
        return doc, None
    if req.env_map_yaml:
        try:
            doc = yaml.safe_load(req.env_map_yaml)
        except yaml.YAMLError as e:
            return None, f"Invalid env-map YAML: {e}"
        if not isinstance(doc, dict):
            return None, "Pasted env-map YAML must be a mapping."
        return doc, None
    return None, None


def _do_render(req: RenderRequest) -> dict[str, Any]:
    try:
        check_helm()
    except HelmError as e:
        return {"ok": False, "trees": None, "error": str(e)}

    path = Path(req.path).expanduser()

    if path.is_dir():
        chart_dir = path.resolve()
        if not (chart_dir / "Chart.yaml").exists():
            return {"ok": False, "trees": None, "error": f"{chart_dir} has no Chart.yaml"}
        root_node = _chart_root_node(chart_dir, req.values_override)
        root_dir = chart_dir.parent
    else:
        try:
            doc = load_yaml_file(path)
            root_node = parse_application(doc)
        except ParseError as e:
            return {"ok": False, "trees": None, "error": str(e)}
        root_dir = path.resolve().parent

    env_map_doc, error = _load_env_map_doc(req)
    if error:
        return {"ok": False, "trees": None, "error": error}
    if env_map_doc is not None:
        return _do_render_with_env_map(root_node, root_dir, env_map_doc, req)

    with tempfile.TemporaryDirectory(prefix="argocheck-") as tmp:
        tmp_dir = Path(tmp)
        root_node = walk(
            root_node,
            tmp_dir=tmp_dir,
            argocd_env=req.argocd_env,
            max_depth=req.max_depth,
            _parent_chart_dir=root_dir,
        )
        tree = _ser_node(root_node, tmp_dir)

    return {"ok": True, "trees": [tree], "error": None, "valueTree": False, "leaves": []}


def _do_render_with_env_map(
    root_node: AppNode, root_dir: Path, env_map_doc: dict[str, Any], req: RenderRequest
) -> dict[str, Any]:
    try:
        leaves = parse_leaves(env_map_doc)
    except ValueTreeError as e:
        return {"ok": False, "trees": None, "error": str(e)}

    all_paths = [leaf.display_path for leaf in leaves]

    if req.selected_leaves is None:
        # Phase 1: enumerate only — cheap, no helm invocations. The client
        # shows a checkbox tree and asks again with an explicit selection.
        return {"ok": True, "trees": None, "error": None, "valueTree": True, "leaves": all_paths}

    selected = set(req.selected_leaves)
    chosen = [leaf for leaf in leaves if leaf.display_path in selected]
    if not chosen:
        return {"ok": False, "trees": None, "error": "No leaves selected."}

    # Each leaf is a full standalone instance of root_node, not a child of
    # it — there's no shared parent app to nest them under, so every leaf is
    # rendered and returned as its own independent root tree.
    leaf_nodes = [build_leaf_node(root_node, leaf) for leaf in chosen]

    with tempfile.TemporaryDirectory(prefix="argocheck-") as tmp:
        tmp_dir = Path(tmp)
        for leaf_node in leaf_nodes:
            walk(
                leaf_node,
                tmp_dir=tmp_dir,
                argocd_env=req.argocd_env,
                max_depth=req.max_depth,
                _parent_chart_dir=root_dir,
            )
        trees = [_ser_node(n, tmp_dir) for n in leaf_nodes]

    return {"ok": True, "trees": trees, "error": None, "valueTree": True, "leaves": all_paths}


# ── API routes ────────────────────────────────────────────────────────────────

@app.post("/api/render")
async def api_render(req: RenderRequest) -> dict[str, Any]:
    _recents.add(str(Path(req.path).resolve()))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, _do_render, req)


@app.get("/api/recents")
def api_recents() -> list[str]:
    return _recents.load()


@app.delete("/api/recents")
def api_delete_recent(path: str = Query(...)) -> dict[str, bool]:
    _recents.remove(path)
    return {"ok": True}


@app.get("/api/browse")
def api_browse(path: str = Query(default="~")) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        p = p.parent
    try:
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        return {"error": "Permission denied", "current": str(p),
                "parent": str(p.parent) if p.parent != p else None,
                "dirs": [], "files": [], "hasChart": False}
    dirs = [str(e) for e in entries if e.is_dir() and not e.name.startswith(".")]
    files = [str(e) for e in entries if e.is_file() and e.suffix in (".yaml", ".yml")]
    return {
        "current": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs,
        "files": files,
        "hasChart": (p / "Chart.yaml").exists(),
        "error": None,
    }


# ── Static file routes ────────────────────────────────────────────────────────

@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# ── Entry point ───────────────────────────────────────────────────────────────

import click

@click.command()
@click.option("--port", default=8765, show_default=True, help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind to.")
@click.option("--no-browser", is_flag=True, default=False, help="Do not open a browser tab.")
def run(port: int, host: str, no_browser: bool) -> None:
    """Start the argocheck web interface."""
    import threading
    url = f"http://{host}:{port}"
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    click.echo(f"argocheck listening on {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
