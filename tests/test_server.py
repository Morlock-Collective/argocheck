"""Tests for the FastAPI server's tree serialization."""
import tempfile
from pathlib import Path

from argocheck.models import AppNode, HelmSource
from argocheck.server import _ser_node


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
