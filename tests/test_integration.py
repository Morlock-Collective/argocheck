"""Integration tests: real helm invocations against fixture charts."""
import tempfile
from pathlib import Path

import pytest

from argocheck.helm import run_template
from argocheck.models import AppNode, HelmParameter, HelmSource
from argocheck.parser import load_yaml_file, parse_application
from argocheck.walker import walk

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
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 1
    assert result.manifests[0]["kind"] == "Deployment"
    assert result.manifests[0]["spec"]["replicas"] == 3  # from --set replicaCount=3


def test_walk_app_of_apps():
    doc = load_yaml_file(FIXTURES / "root-app-parent.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "parent-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 0
    assert len(result.children) == 1

    child = result.children[0]
    assert child.name == "child-app"
    assert child.error is None
    assert len(child.manifests) == 1
    assert child.manifests[0]["kind"] == "ConfigMap"
    assert child.manifests[0]["data"]["replicaCount"] == "2"


def test_walk_cycle_detection():
    src = HelmSource(repo_url=str(FIXTURES / "simple-chart"))
    node = AppNode(name="self-ref", namespace="default", sources=[src])

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp), _visited={"self-ref"})

    assert result.error is not None
    assert "Cycle" in str(result.error)


def test_walk_plain_manifests():
    """Plain directory source (no Chart.yaml): YAML files are read directly."""
    doc = load_yaml_file(FIXTURES / "root-app-plain.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "plain-manifests")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    kinds = {m["kind"] for m in result.manifests}
    assert kinds == {"Namespace", "ConfigMap"}
    # Files from both top-level and subdirectory are included
    assert len(result.manifests) == 2


def test_walk_multi_source_with_ref_values():
    """Multi-source app: one chart source + one ref source providing a values file."""
    doc = load_yaml_file(FIXTURES / "root-app-multisource.yaml")
    node = parse_application(doc)
    # Point both sources to local fixture paths
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")
    node.sources[1].repo_url = str(FIXTURES / "multisource-values")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 1
    assert result.manifests[0]["kind"] == "Deployment"
    # The ref values file sets replicaCount: 4
    assert result.manifests[0]["spec"]["replicas"] == 4


def _multi_chart_manifests(result):
    by_kind = {m["kind"]: m for m in result.manifests}
    assert set(by_kind) == {"Deployment", "ConfigMap"}
    return by_kind


def test_walk_multi_source_two_charts():
    """Two real chart sources (no ref) are both rendered and merged, each keeping
    its own release name and parameter values."""
    doc = load_yaml_file(FIXTURES / "root-app-multi-chart.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")
    node.sources[1].repo_url = str(FIXTURES / "child-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 2
    by_kind = _multi_chart_manifests(result)

    deployment = by_kind["Deployment"]
    assert deployment["metadata"]["name"] == "chart-a-nginx"
    assert deployment["spec"]["replicas"] == 2

    configmap = by_kind["ConfigMap"]
    assert configmap["metadata"]["name"] == "chart-b-config"
    assert configmap["data"]["replicaCount"] == "5"


def test_walk_multi_source_chart_dir_is_first_source():
    """node.chart_dir should resolve to the first source's directory, used as the
    parent chart dir when recursing into child Applications."""
    doc = load_yaml_file(FIXTURES / "root-app-multi-chart.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")
    node.sources[1].repo_url = str(FIXTURES / "child-chart")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert result.chart_dir == (FIXTURES / "simple-chart").resolve()


def test_walk_multi_source_all_ref_only_errors():
    """An app whose sources are all ref-only (no chart/path) has nothing to render."""
    doc = load_yaml_file(FIXTURES / "root-app-all-ref.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "multisource-values")
    node.sources[1].repo_url = str(FIXTURES / "multisource-values")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is not None
    assert "no renderable source found" in str(result.error)
    assert len(result.manifests) == 0


def test_walk_multi_source_three_sources_mixed():
    """Two chart sources plus a shared ref-values source: only the chart that
    references the ref picks up the overridden value, the other keeps its default."""
    doc = load_yaml_file(FIXTURES / "root-app-multi-chart-with-ref.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")
    node.sources[1].repo_url = str(FIXTURES / "child-chart")
    node.sources[2].repo_url = str(FIXTURES / "multisource-values")

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert len(result.manifests) == 2
    by_kind = _multi_chart_manifests(result)

    # simple-chart consumes $sharedvals/overrides.yaml -> replicaCount: 4
    assert by_kind["Deployment"]["spec"]["replicas"] == 4
    # child-chart does not reference the ref -> keeps its own default (1)
    assert by_kind["ConfigMap"]["data"]["replicaCount"] == "1"
