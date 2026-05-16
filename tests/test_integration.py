"""Integration tests: real helm invocations against fixture charts."""
import tempfile
from pathlib import Path

import pytest

from localargo.helm import run_template
from localargo.models import HelmParameter, HelmSource
from localargo.parser import load_yaml_file, parse_application
from localargo.walker import walk

FIXTURES = Path(__file__).parent / "fixtures"


def _simple_source(chart_name: str = "simple-chart", **kwargs) -> HelmSource:
    return HelmSource(repo_url=str(FIXTURES / chart_name), **kwargs)


def test_helm_template_simple():
    with tempfile.TemporaryDirectory() as tmp:
        src = _simple_source()
        docs = run_template(
            chart_path=FIXTURES / "simple-chart",
            source=src,
            release_name="test",
            namespace="default",
            tmp_dir=Path(tmp),
        )
    assert len(docs) == 1
    assert docs[0]["kind"] == "Deployment"
    assert docs[0]["metadata"]["name"] == "test-nginx"


def test_helm_template_with_parameter():
    with tempfile.TemporaryDirectory() as tmp:
        src = _simple_source(parameters=[HelmParameter("replicaCount", "5")])
        docs = run_template(
            chart_path=FIXTURES / "simple-chart",
            source=src,
            release_name="test",
            namespace="default",
            tmp_dir=Path(tmp),
        )
    assert docs[0]["spec"]["replicas"] == 5


def test_helm_template_with_inline_values():
    with tempfile.TemporaryDirectory() as tmp:
        src = _simple_source(values="replicaCount: 7\n")
        docs = run_template(
            chart_path=FIXTURES / "simple-chart",
            source=src,
            release_name="test",
            namespace="default",
            tmp_dir=Path(tmp),
        )
    assert docs[0]["spec"]["replicas"] == 7


def test_walk_simple_app():
    doc = load_yaml_file(FIXTURES / "root-app-simple.yaml")
    node = parse_application(doc)
    # Point repoURL to absolute fixture path for the integration test
    node.source.repo_url = str(FIXTURES / "simple-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 1
    assert result.manifests[0]["kind"] == "Deployment"
    assert result.manifests[0]["spec"]["replicas"] == 3  # from --set replicaCount=3


def test_walk_app_of_apps():
    doc = load_yaml_file(FIXTURES / "root-app-parent.yaml")
    node = parse_application(doc)
    node.source.repo_url = str(FIXTURES / "parent-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    # Parent renders only Applications, no direct resources
    assert len(result.manifests) == 0
    assert len(result.children) == 1

    child = result.children[0]
    assert child.name == "child-app"
    assert child.error is None
    assert len(child.manifests) == 1
    assert child.manifests[0]["kind"] == "ConfigMap"
    # replicaCount=2 was passed via parameter from parent chart values
    assert child.manifests[0]["data"]["replicaCount"] == "2"


def test_walk_cycle_detection():
    from localargo.models import AppNode

    src = HelmSource(repo_url=str(FIXTURES / "simple-chart"))
    node = AppNode(name="self-ref", namespace="default", source=src)

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp), _visited={"self-ref"})

    assert result.error is not None
    assert "Cycle" in str(result.error)
