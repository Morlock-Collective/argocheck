"""Tests for argocheck.models."""
import pytest

from argocheck.models import HelmSource


@pytest.mark.parametrize("url", [
    "./charts/my-app",
    "../sibling/chart",
    "/abs/path/to/chart",
    "file:///abs/path/to/chart",
    "file://./relative-looking-but-still-local",
])
def test_is_local_path_true_for_filesystem_paths(url):
    assert HelmSource(repo_url=url).is_local_path is True


@pytest.mark.parametrize("url", [
    "https://github.com/my-org/my-repo.git",
    "http://charts.example.com",
    "oci://registry-1.docker.io/bitnamicharts",
    "git@github.com:my-org/my-repo.git",
])
def test_is_local_path_false_for_remotes(url):
    assert HelmSource(repo_url=url).is_local_path is False
