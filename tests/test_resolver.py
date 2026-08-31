"""Tests for source resolution, including revision-aware local git repos."""
import subprocess
import tempfile
from pathlib import Path

import pytest

from argocheck.models import HelmSource
from argocheck.resolver import ResolveError, _slug, resolve_source


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
