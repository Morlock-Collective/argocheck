"""Tests for argocheck.cli helpers."""
from argocheck.cli import _local_path_app_names
from argocheck.models import AppNode, HelmSource


def _node(name, repo_url, app_manifest="present", children=None):
    return AppNode(
        name=name,
        namespace="default",
        sources=[HelmSource(repo_url=repo_url)],
        app_manifest={"kind": "Application"} if app_manifest else None,
        children=children or [],
    )


def test_no_local_path_sources_returns_empty_set():
    root = _node("root", "https://github.com/my-org/my-repo.git")
    assert _local_path_app_names(root) == set()


def test_root_with_local_path_is_flagged():
    root = _node("root", "./charts/my-app")
    assert _local_path_app_names(root) == {"root"}


def test_child_with_local_path_is_flagged_alongside_root():
    child = _node("child", "./charts/child-app")
    root = _node("root", "https://github.com/my-org/my-repo.git", children=[child])
    assert _local_path_app_names(root) == {"child"}


def test_mixed_tree_collects_every_affected_app():
    grandchild = _node("grandchild", "./charts/grandchild")
    child = _node("child", "https://github.com/my-org/my-repo.git", children=[grandchild])
    root = _node("root", "./charts/root", children=[child])
    assert _local_path_app_names(root) == {"root", "grandchild"}


def test_synthetic_root_with_no_app_manifest_is_never_flagged():
    """A bare chart directory (the web interface's "point directly at a
    chart" entry point) has no Application manifest at all — nothing there
    to warn about "committing this manifest"."""
    root = _node("root", "./some/chart", app_manifest=None)
    assert _local_path_app_names(root) == set()
