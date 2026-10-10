"""Tests for source resolution, including revision-aware local git repos."""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from argocheck import resolver
from argocheck.models import HelmSource
from argocheck.resolver import ResolveError, _build_git_ssh_env, _resolve_git, _slug, resolve_source


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def local_repo():
    """A local git repo with two commits on main and a divergent branch."""
    with tempfile.TemporaryDirectory() as repo_dir:
        repo = Path(repo_dir)
        _git("init", "--quiet", "--initial-branch=main", cwd=repo)
        _git("config", "user.email", "test@example.com", cwd=repo)
        _git("config", "user.name", "Test", cwd=repo)

        (repo / "Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 0.1.0\n")
        (repo / "values.yaml").write_text("replicaCount: 1\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "--quiet", "-m", "v1: replicaCount 1", cwd=repo)
        v1_sha = _git("rev-parse", "HEAD", cwd=repo)

        (repo / "values.yaml").write_text("replicaCount: 2\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "--quiet", "-m", "v2: replicaCount 2", cwd=repo)

        _git("branch", "feature", v1_sha, cwd=repo)

        yield repo, v1_sha


def test_resolve_local_git_head_uses_working_tree(local_repo):
    """targetRevision HEAD (the default) keeps today's behavior: use the working
    tree as-is, including any uncommitted changes."""
    repo, _ = local_repo
    (repo / "values.yaml").write_text("replicaCount: 99\n")  # uncommitted

    src = HelmSource(repo_url=str(repo), target_revision="HEAD")
    with tempfile.TemporaryDirectory() as tmp:
        resolved = resolve_source(src, tmp_dir=Path(tmp))

    assert resolved == repo
    assert (resolved / "values.yaml").read_text() == "replicaCount: 99\n"


def test_resolve_local_git_specific_revision_checks_out_commit(local_repo):
    """A concrete targetRevision on a local git repo resolves to a clone checked
    out at that commit, not the live working tree."""
    repo, v1_sha = local_repo

    src = HelmSource(repo_url=str(repo), target_revision="feature")
    with tempfile.TemporaryDirectory() as tmp:
        resolved = resolve_source(src, tmp_dir=Path(tmp))

        assert resolved != repo
        assert (resolved / "values.yaml").read_text() == "replicaCount: 1\n"
        assert _git("rev-parse", "HEAD", cwd=resolved) == v1_sha


def test_resolve_local_git_two_revisions_of_same_repo_do_not_conflict(local_repo):
    """The same local repo referenced at two different revisions (a real
    multi-source use case) must resolve to two independent directories."""
    repo, _ = local_repo

    src_main = HelmSource(repo_url=str(repo), target_revision="main")
    src_feature = HelmSource(repo_url=str(repo), target_revision="feature")

    with tempfile.TemporaryDirectory() as tmp:
        resolved_main = resolve_source(src_main, tmp_dir=Path(tmp))
        resolved_feature = resolve_source(src_feature, tmp_dir=Path(tmp))

        assert resolved_main != resolved_feature
        assert (resolved_main / "values.yaml").read_text() == "replicaCount: 2\n"
        assert (resolved_feature / "values.yaml").read_text() == "replicaCount: 1\n"


def test_resolve_local_git_reclones_when_branch_moves(local_repo):
    """If a branch's tip moves between calls (e.g. new local commits), the cached
    clone must be refreshed rather than silently reused at the stale commit."""
    repo, _ = local_repo

    src = HelmSource(repo_url=str(repo), target_revision="feature")
    with tempfile.TemporaryDirectory() as tmp:
        first = resolve_source(src, tmp_dir=Path(tmp))
        assert (first / "values.yaml").read_text() == "replicaCount: 1\n"

        (repo / "values.yaml").write_text("replicaCount: 42\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "--quiet", "-m", "move feature forward", cwd=repo)
        _git("branch", "-f", "feature", cwd=repo)

        second = resolve_source(src, tmp_dir=Path(tmp))
        assert (second / "values.yaml").read_text() == "replicaCount: 42\n"


def test_resolve_local_git_unknown_revision_raises(local_repo):
    repo, _ = local_repo
    src = HelmSource(repo_url=str(repo), target_revision="does-not-exist")

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ResolveError, match="does-not-exist"):
            resolve_source(src, tmp_dir=Path(tmp))


def test_resolve_local_git_recovers_from_corrupt_cache(local_repo):
    """A corrupt/partial cache directory (e.g. left behind by a killed previous
    run) must be discarded and re-cloned, not crash with an unhandled error."""
    repo, v1_sha = local_repo
    src = HelmSource(repo_url=str(repo), target_revision="feature")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Pre-populate the cache slot with a non-git directory to simulate corruption.
        dest = tmp_dir / "local-git" / _slug(f"{repo}@feature")
        dest.mkdir(parents=True)
        (dest / "garbage.txt").write_text("not a git repo")

        resolved = resolve_source(src, tmp_dir=tmp_dir)

        assert resolved == dest
        assert (resolved / "values.yaml").read_text() == "replicaCount: 1\n"
        assert _git("rev-parse", "HEAD", cwd=resolved) == v1_sha


def test_resolve_local_git_ignore_target_revision_uses_working_tree(local_repo):
    """ignore_target_revision=True resolves a local git repo as if
    targetRevision were HEAD, regardless of what the source actually declares."""
    repo, _ = local_repo
    (repo / "values.yaml").write_text("replicaCount: 99\n")  # uncommitted

    src = HelmSource(repo_url=str(repo), target_revision="feature")
    with tempfile.TemporaryDirectory() as tmp:
        resolved = resolve_source(src, tmp_dir=Path(tmp), ignore_target_revision=True)

    assert resolved == repo
    assert (resolved / "values.yaml").read_text() == "replicaCount: 99\n"


def test_resolve_local_non_git_directory_ignores_revision():
    """A plain (non-git) local directory has no revision concept: targetRevision
    is ignored and the directory is used as-is, same as before this feature."""
    with tempfile.TemporaryDirectory() as plain_dir:
        plain = Path(plain_dir)
        (plain / "Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 0.1.0\n")

        src = HelmSource(repo_url=str(plain), target_revision="some-branch")
        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve_source(src, tmp_dir=Path(tmp))

        assert resolved == plain


@pytest.fixture(autouse=True)
def _reset_ssh_module_state():
    """Each test starts from the default (non-interactive) mode with no
    ssh-agent assumed primed, and restores it afterwards so these tests never
    leak state into other test modules."""
    saved_interactive = resolver._INTERACTIVE
    saved_primed = resolver._agent_primed
    resolver._INTERACTIVE = False
    resolver._agent_primed = False
    yield
    resolver._INTERACTIVE = saved_interactive
    resolver._agent_primed = saved_primed


def test_build_git_ssh_env_non_interactive_uses_batch_mode():
    resolver.configure_ssh_handling(interactive=False)
    env = _build_git_ssh_env()
    assert "-o BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "-o ConnectTimeout=10" in env["GIT_SSH_COMMAND"]
    assert "SSH_ASKPASS" not in env


def test_build_git_ssh_env_interactive_uses_askpass():
    resolver.configure_ssh_handling(interactive=True)
    env = _build_git_ssh_env()
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["DISPLAY"]
    assert "-o ConnectTimeout=10" in env["GIT_SSH_COMMAND"]
    askpass_path = Path(env["SSH_ASKPASS"])
    assert askpass_path.exists()


def test_build_git_ssh_env_connect_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("ARGOCHECK_SSH_CONNECT_TIMEOUT", "30")
    resolver.configure_ssh_handling(interactive=False)
    env = _build_git_ssh_env()
    assert "-o ConnectTimeout=30" in env["GIT_SSH_COMMAND"]


def test_build_git_ssh_env_connect_timeout_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("ARGOCHECK_SSH_CONNECT_TIMEOUT", "not-a-number")
    resolver.configure_ssh_handling(interactive=False)
    env = _build_git_ssh_env()
    assert "-o ConnectTimeout=10" in env["GIT_SSH_COMMAND"]


def test_resolve_git_timeout_raises_resolve_error_without_hanging():
    resolver.configure_ssh_handling(interactive=False)
    src = HelmSource(repo_url="git@example.com:org/repo.git", chart="demo")

    with patch(
        "argocheck.resolver.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=120),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ResolveError, match="timed out"):
                _resolve_git(src, Path(tmp), require_chart=True)


def test_resolve_git_non_interactive_auth_failure_includes_ssh_add_hint():
    resolver.configure_ssh_handling(interactive=False)
    src = HelmSource(repo_url="git@example.com:org/repo.git", chart="demo")

    error = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "clone"],
        output="", stderr="Permission denied (publickey).\n",
    )
    with patch("argocheck.resolver.subprocess.run", side_effect=error):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ResolveError, match="ssh-add"):
                _resolve_git(src, Path(tmp), require_chart=True)


def test_resolve_git_https_url_never_touches_ssh_env():
    """An https:// source must never spawn an ssh-agent or set SSH-specific
    env — only git@/ssh:// URLs are SSH-authenticated."""
    resolver.configure_ssh_handling(interactive=True)
    src = HelmSource(repo_url="https://example.com/org/repo.git", chart="demo")

    with patch("argocheck.resolver.subprocess.run") as run, \
         patch("argocheck.resolver._ensure_ssh_agent_primed") as primed:
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            (tmp_dir / "git" / _slug(f"{src.repo_url}@HEAD")).mkdir(parents=True)
            Path(tmp_dir / "git" / _slug(f"{src.repo_url}@HEAD") / "Chart.yaml").write_text("x")
            _resolve_git(src, tmp_dir, require_chart=False)

        primed.assert_not_called()


def test_ensure_ssh_agent_primed_skips_spawn_when_agent_already_reachable():
    resolver.configure_ssh_handling(interactive=True)

    with patch("argocheck.resolver.subprocess.run") as run:
        run.return_value.returncode = 0  # ssh-add -l: agent reachable
        resolver._ensure_ssh_agent_primed()

        spawn_calls = [c for c in run.call_args_list if c.args[0][0] == "ssh-agent"]
        assert spawn_calls == []
