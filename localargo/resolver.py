"""Resolve an Application source to a local chart directory."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from .helm import HelmError, helm_pull, helm_repo_add, helm_repo_update
from .models import HelmSource


class ResolveError(Exception):
    pass


def resolve_source(source: HelmSource, tmp_dir: Path, working_dir: Path | None = None) -> Path:
    """
    Return the local path to the chart directory for the given source.

    working_dir is the directory of the parent chart (for resolving relative repoURLs).
    """
    repo_url = source.repo_url

    # Local path (absolute, relative, or file:// URI)
    if repo_url.startswith("file://"):
        repo_url = repo_url[len("file://"):]

    if _is_local(repo_url):
        return _resolve_local(repo_url, source, working_dir)

    # Helm chart repository (HTTP/HTTPS with chart name set, or OCI)
    if source.chart and (repo_url.startswith("oci://") or _is_http(repo_url)):
        return _resolve_helm_repo(source, tmp_dir)

    # Git repository
    if _is_git(repo_url):
        return _resolve_git(source, tmp_dir)

    # Fallback: treat as local
    return _resolve_local(repo_url, source, working_dir)


def _is_local(url: str) -> bool:
    return url.startswith("/") or url.startswith("./") or url.startswith("../")


def _is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _is_git(url: str) -> bool:
    return (
        url.startswith("git@")
        or url.endswith(".git")
        or (_is_http(url) and not _looks_like_helm_repo(url))
    )


def _looks_like_helm_repo(url: str) -> bool:
    # Helm repos typically have /index.yaml; we can't easily tell without fetching.
    # Heuristic: if chart is set alongside an HTTP URL, treat as helm repo.
    return True  # caller checks source.chart first


def _resolve_local(repo_url: str, source: HelmSource, working_dir: Path | None) -> Path:
    base = working_dir or Path.cwd()
    chart_base = Path(repo_url) if Path(repo_url).is_absolute() else base / repo_url

    if source.path:
        chart_path = chart_base / source.path
    else:
        chart_path = chart_base

    chart_path = chart_path.resolve()
    if not chart_path.exists():
        raise ResolveError(f"Local chart path does not exist: {chart_path}")
    if not (chart_path / "Chart.yaml").exists():
        raise ResolveError(f"No Chart.yaml found in {chart_path}")
    return chart_path


def _resolve_helm_repo(source: HelmSource, tmp_dir: Path) -> Path:
    assert source.chart is not None

    repo_url = source.repo_url
    chart_name = source.chart
    version = source.target_revision if source.target_revision not in ("HEAD", "") else None

    if repo_url.startswith("oci://"):
        chart_ref = f"{repo_url.rstrip('/')}/{chart_name}"
        pull_dir = tmp_dir / "charts" / _slug(chart_ref)
        pull_dir.mkdir(parents=True, exist_ok=True)
        helm_pull(chart_ref, version, pull_dir)
    else:
        repo_name = "localargo-" + _slug(repo_url)
        helm_repo_add(repo_name, repo_url)
        helm_repo_update()
        chart_ref = f"{repo_name}/{chart_name}"
        pull_dir = tmp_dir / "charts" / _slug(chart_ref)
        pull_dir.mkdir(parents=True, exist_ok=True)
        helm_pull(chart_ref, version, pull_dir)

    # After untar, the chart lives in a subdirectory named after the chart
    candidates = [p for p in pull_dir.iterdir() if p.is_dir() and (p / "Chart.yaml").exists()]
    if not candidates:
        raise ResolveError(f"helm pull did not produce a chart directory in {pull_dir}")
    return candidates[0]


def _resolve_git(source: HelmSource, tmp_dir: Path) -> Path:
    repo_url = source.repo_url
    revision = source.target_revision or "HEAD"

    clone_dir = tmp_dir / "git" / _slug(repo_url)
    clone_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", "1"]
    if revision and revision != "HEAD":
        cmd += ["--branch", revision]
    cmd += [repo_url, str(clone_dir)]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise ResolveError("git binary not found. Install git or use a local chart path.")
    except subprocess.CalledProcessError as e:
        raise ResolveError(f"git clone failed for {repo_url!r}:\n{e.stderr}")

    if source.path:
        chart_path = clone_dir / source.path
    else:
        chart_path = clone_dir

    if not chart_path.exists():
        raise ResolveError(f"path {source.path!r} not found in cloned repo {repo_url}")
    if not (chart_path / "Chart.yaml").exists():
        raise ResolveError(f"No Chart.yaml at {chart_path}")

    return chart_path


def _slug(url: str) -> str:
    """Short stable directory name derived from a URL."""
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in url[-30:])
    return f"{safe}-{h}"
