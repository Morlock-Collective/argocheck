"""Resolve an Application source to a local chart directory."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .helm import HelmError, helm_pull, helm_repo_add, helm_repo_update
from .models import HelmSource


class ResolveError(Exception):
    pass


def resolve_source(
    source: HelmSource,
    tmp_dir: Path,
    working_dir: Path | None = None,
    require_chart: bool = True,
) -> Path:
    """
    Return the local path to the chart (or values) directory for the given source.

    working_dir is the directory of the parent chart (for resolving relative repoURLs).
    require_chart=False skips the Chart.yaml check — used for ref-only value sources.
    """
    repo_url = source.repo_url

    if repo_url.startswith("file://"):
        repo_url = repo_url[len("file://"):]

    if _is_local(repo_url):
        return _resolve_local(repo_url, source, working_dir, require_chart)

    if source.chart and (repo_url.startswith("oci://") or _is_http(repo_url)):
        return _resolve_helm_repo(source, tmp_dir)

    if _is_git(repo_url):
        return _resolve_git(source, tmp_dir, require_chart)

    return _resolve_local(repo_url, source, working_dir, require_chart)


def resolve_ref_map(
    sources: list[HelmSource],
    tmp_dir: Path,
    working_dir: Path | None = None,
) -> dict[str, Path]:
    """
    Resolve all sources that have a `ref` field to local directories.
    Returns a mapping of ref-name → local path, used to expand $ref/path in valueFiles.
    """
    ref_map: dict[str, Path] = {}
    for src in sources:
        if src.ref:
            local_dir = resolve_source(
                src, tmp_dir=tmp_dir, working_dir=working_dir, require_chart=False
            )
            ref_map[src.ref] = local_dir
    return ref_map


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
    return True  # caller checks source.chart first


def _resolve_local(
    repo_url: str,
    source: HelmSource,
    working_dir: Path | None,
    require_chart: bool,
) -> Path:
    base = working_dir or Path.cwd()
    chart_base = Path(repo_url) if Path(repo_url).is_absolute() else base / repo_url

    chart_path = (chart_base / source.path) if source.path else chart_base
    chart_path = chart_path.resolve()

    if not chart_path.exists():
        raise ResolveError(f"Local chart path does not exist: {chart_path}")
    if require_chart and not (chart_path / "Chart.yaml").exists():
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

    candidates = [p for p in pull_dir.iterdir() if p.is_dir() and (p / "Chart.yaml").exists()]
    if not candidates:
        raise ResolveError(f"helm pull did not produce a chart directory in {pull_dir}")
    return candidates[0]


def _resolve_git(source: HelmSource, tmp_dir: Path, require_chart: bool) -> Path:
    repo_url = source.repo_url
    revision = source.target_revision or "HEAD"

    # Key on both URL and revision so different branches never share a directory,
    # and the same URL+revision is reused within a single run without re-cloning.
    clone_dir = tmp_dir / "git" / _slug(f"{repo_url}@{revision}")

    if not (clone_dir / ".git" / "HEAD").exists():
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

    chart_path = (clone_dir / source.path) if source.path else clone_dir

    if not chart_path.exists():
        raise ResolveError(f"path {source.path!r} not found in cloned repo {repo_url}")
    if require_chart and not (chart_path / "Chart.yaml").exists():
        raise ResolveError(f"No Chart.yaml at {chart_path}")

    return chart_path


def _slug(url: str) -> str:
    """Short stable directory name derived from a URL."""
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in url[-30:])
    return f"{safe}-{h}"
