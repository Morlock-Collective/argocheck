"""Tests for the FastAPI server's tree serialization and value-tree rendering."""
import tempfile
from pathlib import Path

from argocheck.models import AppNode, HelmSource
from argocheck.server import RenderRequest, _do_render, _ser_node

FIXTURES = Path(__file__).parent / "fixtures"


def _node(name: str, chart_dir: Path | None) -> AppNode:
    return AppNode(
        name=name,
        namespace="default",
        sources=[HelmSource(repo_url=".")],
        chart_dir=chart_dir,
    )


def test_ser_node_hides_chart_dir_under_tmp_dir():
    """chart_dir pointing into the request's scratch tmp_dir is deleted by the
    time the response reaches the client, so it must not be surfaced as-is."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        scratch_chart_dir = tmp_dir / "git" / "some-clone"
        scratch_chart_dir.mkdir(parents=True)

        node = _node("app", scratch_chart_dir)
        out = _ser_node(node, tmp_dir)

    assert out["chartDir"] is None


def test_ser_node_keeps_chart_dir_outside_tmp_dir():
    """A chart_dir on a durable path (e.g. a plain local source) is unaffected."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        tmp_dir = Path(tmp)
        durable_chart_dir = Path(other)

        node = _node("app", durable_chart_dir)
        out = _ser_node(node, tmp_dir)

    assert out["chartDir"] == str(durable_chart_dir)


def test_ser_node_handles_missing_chart_dir():
    with tempfile.TemporaryDirectory() as tmp:
        node = _node("app", None)
        out = _ser_node(node, Path(tmp))

    assert out["chartDir"] is None


def test_ser_node_recurses_into_children():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        scratch = tmp_dir / "git" / "child-clone"
        scratch.mkdir(parents=True)

        child = _node("child", scratch)
        root = _node("root", None)
        root.children.append(child)

        out = _ser_node(root, tmp_dir)

    assert out["children"][0]["name"] == "child"
    assert out["children"][0]["chartDir"] is None


def test_do_render_plain_app_ignores_env_map_fields_when_unset():
    """A normal render (no env_map_path/env_map_yaml) behaves exactly as
    before — never enters value-tree territory."""
    req = RenderRequest(path=str(FIXTURES / "root-app-simple.yaml"))
    result = _do_render(req)

    assert result["ok"] is True
    assert result["valueTree"] is False
    assert result["tree"]["manifests"][0]["kind"] == "Deployment"


def test_do_render_env_map_path_without_selection_only_enumerates():
    """The first call (no selected_leaves) must not run helm at all — just
    enumerate leaves so the client can show a checkbox tree cheaply."""
    req = RenderRequest(
        path=str(FIXTURES / "root-app-simple.yaml"),
        env_map_path=str(FIXTURES / "value-tree-clusters.yaml"),
    )
    result = _do_render(req)

    assert result["ok"] is True
    assert result["valueTree"] is True
    assert result["tree"] is None
    assert set(result["leaves"]) == {"prod/ns-a", "prod/ns-b", "qa/ns-a"}


def test_do_render_env_map_path_with_selection_renders_only_those_leaves_with_priority():
    """Tree-supplied replicaCount must override the base app's own
    helm.parameters replicaCount=3 (root-app-simple.yaml)."""
    req = RenderRequest(
        path=str(FIXTURES / "root-app-simple.yaml"),
        env_map_path=str(FIXTURES / "value-tree-clusters.yaml"),
        selected_leaves=["prod/ns-b", "qa/ns-a"],
    )
    result = _do_render(req)

    assert result["ok"] is True
    assert result["valueTree"] is True
    children = {c["name"]: c for c in result["tree"]["children"]}
    assert set(children) == {"prod-ns-b", "qa-ns-a"}
    for child in children.values():
        assert child["error"] is None
        assert child["manifests"][0]["kind"] == "Deployment"
    assert children["prod-ns-b"]["manifests"][0]["spec"]["replicas"] == 5
    assert children["qa-ns-a"]["manifests"][0]["spec"]["replicas"] == 1


def test_do_render_env_map_yaml_pasted_works_like_path():
    env_map_yaml = (FIXTURES / "value-tree-clusters.yaml").read_text()
    req = RenderRequest(
        path=str(FIXTURES / "root-app-simple.yaml"),
        env_map_yaml=env_map_yaml,
    )
    result = _do_render(req)

    assert result["ok"] is True
    assert result["valueTree"] is True
    assert set(result["leaves"]) == {"prod/ns-a", "prod/ns-b", "qa/ns-a"}


def test_do_render_env_map_both_path_and_yaml_errors():
    req = RenderRequest(
        path=str(FIXTURES / "root-app-simple.yaml"),
        env_map_path=str(FIXTURES / "value-tree-clusters.yaml"),
        env_map_yaml="argocheck_root: x",
    )
    result = _do_render(req)

    assert result["ok"] is False
    assert "not both" in result["error"]


def test_do_render_env_map_empty_selection_errors():
    req = RenderRequest(
        path=str(FIXTURES / "root-app-simple.yaml"),
        env_map_path=str(FIXTURES / "value-tree-clusters.yaml"),
        selected_leaves=["does-not-exist"],
    )
    result = _do_render(req)

    assert result["ok"] is False
    assert "No leaves selected" in result["error"]
