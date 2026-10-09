"""Resolve an Application source to a local chart directory."""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from .helm import HelmError, helm_pull, helm_repo_add, helm_repo_update
from .models import HelmSource
from .plugins import ResolveContext, get_registry


class ResolveError(Exception):
    pass


_GIT_CLONE_TIMEOUT = 120

# Whether the current process has a controlling terminal it can prompt an SSH
# passphrase on (the CLI) or not (the web server, which has no tty to prompt
# against). Set once by cli.py/server.py at startup; defaults to the safe,
# never-hang behavior for anything that doesn't call configure_ssh_handling
# (e.g. tests).
_INTERACTIVE = False

_askpass_script: Path | None = None
_agent_primed = False

_SSH_AUTH_FAILURE_MARKERS = (
    "Permission denied",
    "Host key verification failed",
    "Could not read from remote repository",
)


def configure_ssh_handling(interactive: bool) -> None:
    """
    interactive=True (CLI): a passphrase-protected SSH key is prompted for via
    a real terminal prompt, once per process, cached in an ssh-agent for the
    rest of the run.
    interactive=False (web server): no controlling terminal exists to prompt
    on, so an SSH key that needs a passphrase fails fast with an actionable
    error instead of hanging, unless it's already unlocked in an inherited
    ssh-agent.
    """
    global _INTERACTIVE
    _INTERACTIVE = interactive


def _is_ssh_url(repo_url: str) -> bool:
    return repo_url.startswith("git@") or repo_url.startswith("ssh://")


def _ensure_askpass_script() -> Path:
    global _askpass_script
    if _askpass_script is not None:
        return _askpass_script

    path = Path(tempfile.gettempdir()) / f"argocheck-askpass-{os.getpid()}"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import getpass, sys\n"
        'print(getpass.getpass(sys.argv[1] if len(sys.argv) > 1 else "Passphrase: "))\n',
        encoding="utf-8",
    )
    path.chmod(stat.S_IRWXU)
    atexit.register(path.unlink, missing_ok=True)
    _askpass_script = path
    return path


def _ensure_ssh_agent_primed() -> None:
    """
    Lazily, once per process: make sure an ssh-agent is reachable (spawning
    one if none is) and load default SSH identities into it, prompting via
    the askpass helper if one of them is passphrase-protected. Every later
    SSH-authenticated git clone in this process then authenticates silently
    via that agent.
    """
    global _agent_primed
    if _agent_primed:
        return
    _agent_primed = True

    reachable = subprocess.run(
        ["ssh-add", "-l"], capture_output=True, text=True, encoding="utf-8",
    ).returncode in (0, 1)

    if not reachable:
        try:
            started = subprocess.run(
                ["ssh-agent", "-s"], capture_output=True, text=True,
                encoding="utf-8", check=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            return  # no ssh-agent available; fall through, clone may still work or fail cleanly

        sock = pid = None
        for line in started.splitlines():
            if line.startswith("SSH_AUTH_SOCK="):
                sock = line.split("=", 1)[1].split(";", 1)[0]
            elif line.startswith("SSH_AGENT_PID="):
                pid = line.split("=", 1)[1].split(";", 1)[0]
        if sock:
            os.environ["SSH_AUTH_SOCK"] = sock
        if pid:
            os.environ["SSH_AGENT_PID"] = pid
            atexit.register(
                subprocess.run, ["ssh-agent", "-k"],
                env={**os.environ, "SSH_AGENT_PID": pid}, capture_output=True,
            )

    subprocess.run(
        ["ssh-add"], capture_output=True, text=True, encoding="utf-8",
        env=_build_git_ssh_env(),
    )


def _build_git_ssh_env() -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if _INTERACTIVE:
        env["SSH_ASKPASS"] = str(_ensure_askpass_script())
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = os.environ.get("DISPLAY") or ":0"
    else:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def resolve_source(
    source: HelmSource,
    tmp_dir: Path,
    working_dir: Path | None = None,
    require_chart: bool = True,
    ignore_target_revision: bool = False,
) -> Path:
    """
    Return the local path to the chart (or values) directory for the given source.

    working_dir is the directory of the parent chart (for resolving relative repoURLs).
    require_chart=False skips the Chart.yaml check — used for ref-only value sources.
    ignore_target_revision=True resolves every source as if targetRevision were
    HEAD/unset — e.g. a local git repo's working tree as-is, a remote git repo's
    default branch, a Helm repo chart's latest version — regardless of what the
    Application actually declares. This only changes which source gets resolved
    and rendered, never the rendered output's own values (an --argocd-env
    render's ARGOCD_APP_SOURCE_TARGET_REVISION is unaffected).
    """
    context = ResolveContext(working_dir=working_dir, tmp_dir=tmp_dir, require_chart=require_chart)
    plugin_path = get_registry().resolve_source(source, context)
    if plugin_path is not None:
        return plugin_path

    repo_url = source.repo_url

    if repo_url.startswith("file://"):
        repo_url = repo_url[len("file://"):]

    if _is_local(repo_url):
        return _resolve_local(repo_url, source, working_dir, require_chart, tmp_dir, ignore_target_revision)

    if source.chart and (repo_url.startswith("oci://") or _is_http(repo_url)):
        return _resolve_helm_repo(source, tmp_dir, ignore_target_revision)

    if _is_git(repo_url):
        return _resolve_git(source, tmp_dir, require_chart, ignore_target_revision)

    return _resolve_local(repo_url, source, working_dir, require_chart, tmp_dir, ignore_target_revision)


def resolve_ref_map(
    sources: list[HelmSource],
    tmp_dir: Path,
    working_dir: Path | None = None,
    ignore_target_revision: bool = False,
) -> dict[str, Path]:
    """
    Resolve all sources that have a `ref` field to local directories.
    Returns a mapping of ref-name → local path, used to expand $ref/path in valueFiles.
    """
    ref_map: dict[str, Path] = {}
    for src in sources:
        if src.ref:
            local_dir = resolve_source(
                src, tmp_dir=tmp_dir, working_dir=working_dir, require_chart=False,
                ignore_target_revision=ignore_target_revision,
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
    tmp_dir: Path,
    ignore_target_revision: bool = False,
) -> Path:
    base = working_dir or Path.cwd()
    chart_base = Path(repo_url) if Path(repo_url).is_absolute() else base / repo_url
    chart_base = chart_base.resolve()

    if not chart_base.exists():
        raise ResolveError(f"Local chart path does not exist: {chart_base}")

    revision = "HEAD" if ignore_target_revision else source.target_revision
    if revision not in ("HEAD", "", None) and (chart_base / ".git").exists():
        chart_base = _resolve_local_git(chart_base, revision, tmp_dir)

    chart_path = (chart_base / source.path) if source.path else chart_base
    chart_path = chart_path.resolve()

    if not chart_path.exists():
        raise ResolveError(f"Local chart path does not exist: {chart_path}")
    if require_chart and not (chart_path / "Chart.yaml").exists():
        raise ResolveError(f"No Chart.yaml found in {chart_path}")
    return chart_path


def _resolve_local_git(repo_path: Path, revision: str, tmp_dir: Path) -> Path:
    """
    Clone a local git repo at a specific revision into a scratch directory under
    tmp_dir, so targetRevision is honored for local paths that are themselves git
    repos (instead of always using the working tree as-is).

    Keyed by repo path + revision, so the same local repo referenced multiple
    times at different revisions never collides. The clone is reused across
    calls as long as it's still at the target commit; if the revision has moved
    (e.g. a branch got new commits) since the last run, it's re-cloned.
    """
    try:
        target_sha = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", revision],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        raise ResolveError(
            f"revision {revision!r} not found in local repo {repo_path}:\n{e.stderr}"
        )

    dest = tmp_dir / "local-git" / _slug(f"{repo_path}@{revision}")

    if dest.exists():
        try:
            cached_sha = subprocess.run(
                ["git", "-C", str(dest), "rev-parse", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            # Corrupt or partial cache (e.g. an interrupted previous run) — discard
            # it and clone fresh below rather than crashing.
            cached_sha = None
        if cached_sha == target_sha:
            return dest
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--quiet", str(repo_path), str(dest)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ResolveError(
            f"failed to clone local repo {repo_path}:\n{e.stderr}"
        )

    try:
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", "--detach", target_sha],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(dest)
        raise ResolveError(
            f"failed to check out revision {revision!r} in local repo {repo_path}:\n{e.stderr}"
        )

    return dest


def _resolve_helm_repo(source: HelmSource, tmp_dir: Path, ignore_target_revision: bool = False) -> Path:
    assert source.chart is not None

    repo_url = source.repo_url
    chart_name = source.chart
    version = None if ignore_target_revision else (
        source.target_revision if source.target_revision not in ("HEAD", "") else None
    )

    if repo_url.startswith("oci://"):
        chart_ref = f"{repo_url.rstrip('/')}/{chart_name}"
        pull_dir = tmp_dir / "charts" / _slug(chart_ref)
        pull_dir.mkdir(parents=True, exist_ok=True)
        helm_pull(chart_ref, version, pull_dir)
    else:
        repo_name = "argocheck-" + _slug(repo_url)
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


def _resolve_git(source: HelmSource, tmp_dir: Path, require_chart: bool, ignore_target_revision: bool = False) -> Path:
    repo_url = source.repo_url
    revision = "HEAD" if ignore_target_revision else (source.target_revision or "HEAD")

    # Key on both URL and revision so different branches never share a directory,
    # and the same URL+revision is reused within a single run without re-cloning.
    clone_dir = tmp_dir / "git" / _slug(f"{repo_url}@{revision}")

    if not (clone_dir / ".git" / "HEAD").exists():
        clone_dir.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "clone", "--depth", "1"]
        if revision and revision != "HEAD":
            cmd += ["--branch", revision]
        cmd += [repo_url, str(clone_dir)]

        is_ssh = _is_ssh_url(repo_url)
        if is_ssh and _INTERACTIVE:
            _ensure_ssh_agent_primed()
        env = _build_git_ssh_env() if is_ssh else None

        try:
            subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", check=True,
                env=env, timeout=_GIT_CLONE_TIMEOUT,
            )
        except FileNotFoundError:
            raise ResolveError("git binary not found. Install git or use a local chart path.")
        except subprocess.TimeoutExpired:
            hint = (
                "ssh-add the key first, then retry" if _INTERACTIVE else
                "pre-load it into an ssh-agent before starting argocheck-web: ssh-add <path-to-key>"
            )
            raise ResolveError(
                f"git clone timed out after {_GIT_CLONE_TIMEOUT}s for {repo_url!r}. "
                f"If this repo's SSH key needs a passphrase, {hint}."
            )
        except subprocess.CalledProcessError as e:
            message = f"git clone failed for {repo_url!r}:\n{e.stderr}"
            if (
                not _INTERACTIVE and is_ssh and e.stderr
                and any(marker in e.stderr for marker in _SSH_AUTH_FAILURE_MARKERS)
            ):
                message += (
                    "\nThis SSH key may need a passphrase. Pre-load it before starting "
                    "argocheck-web: ssh-add <path-to-key>"
                )
            raise ResolveError(message)

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
