"""FastAPI web server for localargo."""
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

from localargo import recents as _recents
from localargo.helm import HelmError, check_helm
from localargo.models import AppNode, HelmSource
from localargo.parser import ParseError, load_yaml_file, parse_application
from localargo.walker import walk

_STATIC = Path(__file__).parent / "static"
_EXECUTOR = ThreadPoolExecutor(max_workers=2)

app = FastAPI(title="localargo", docs_url=None, redoc_url=None)


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


def _ser_node(node: AppNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "namespace": node.namespace,
        "sources": [_ser_source(s) for s in node.sources],
        "isMultiSource": node.is_multi_source,
        "manifests": node.manifests,
        "appManifest": node.app_manifest,
        "chartDir": str(node.chart_dir) if node.chart_dir else None,
        "error": _ser_error(node.error),
        "children": [_ser_node(c) for c in node.children],
    }


# ── Render worker (runs in thread pool) ──────────────────────────────────────

class RenderRequest(BaseModel):
    path: str
    argocd_env: bool = False
    max_depth: int = 10
    values_override: str | None = None


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


def _do_render(req: RenderRequest) -> dict[str, Any]:
    try:
        check_helm()
    except HelmError as e:
        return {"ok": False, "tree": None, "error": str(e)}

    path = Path(req.path).expanduser()

    if path.is_dir():
        chart_dir = path.resolve()
        if not (chart_dir / "Chart.yaml").exists():
            return {"ok": False, "tree": None, "error": f"{chart_dir} has no Chart.yaml"}
        root_node = _chart_root_node(chart_dir, req.values_override)
        root_dir = chart_dir.parent
    else:
        try:
            doc = load_yaml_file(path)
            root_node = parse_application(doc)
        except ParseError as e:
            return {"ok": False, "tree": None, "error": str(e)}
        root_dir = path.resolve().parent

    with tempfile.TemporaryDirectory(prefix="localargo-") as tmp:
        root_node = walk(
            root_node,
            tmp_dir=Path(tmp),
            argocd_env=req.argocd_env,
            max_depth=req.max_depth,
            _parent_chart_dir=root_dir,
        )

    return {"ok": True, "tree": _ser_node(root_node), "error": None}


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
    """Start the localargo web interface."""
    import threading
    url = f"http://{host}:{port}"
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    click.echo(f"localargo listening on {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
