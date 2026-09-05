"""Tests for value-tree (environment map) parsing and leaf enumeration."""
import pytest

from argocheck.models import AppNode, HelmSource
from argocheck.valuetree import (
    ValueTreeError,
    build_leaf_node,
    is_value_tree,
    matches_selection,
    parse_leaves,
)


def _doc(**overrides):
    doc = {
        "argocheck_root": "clusters",
        "argocheck_leaf_depth": 2,
        "argocheck_variable_mappings": ["", "cluster", "namespace"],
        "clusters": {
            "prod": {
                "ns-a": {"sourceRepo": "repo-a"},
                "ns-b": {"sourceRepo": "repo-b"},
            },
            "qa": {
                "ns-a": {"sourceRepo": "repo-a-qa"},
            },
        },
    }
    doc.update(overrides)
    return doc


def _base_node(**source_overrides) -> AppNode:
    source = HelmSource(repo_url="./root-chart", target_revision="HEAD", **source_overrides)
    return AppNode(name="my-app", namespace="argocd", sources=[source])


def test_is_value_tree():
    assert is_value_tree(_doc())
    assert not is_value_tree({"kind": "Application"})


def test_parse_leaves_enumerates_all_leaves():
    leaves = parse_leaves(_doc())
    assert {leaf.display_path for leaf in leaves} == {"prod/ns-a", "prod/ns-b", "qa/ns-a"}


def test_leaf_parameters_bind_path_variables_and_own_values():
    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-a")

    params = {p.name: p.value for p in leaf.parameters}
    assert params == {"cluster": "prod", "namespace": "ns-a", "sourceRepo": "repo-a"}


def test_leaf_release_name_is_hyphenated_and_safe():
    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-a")
    assert leaf.release_name == "prod-ns-a"


def test_leaf_parameters_take_priority_over_base_parameters():
    """Tree-supplied parameters must override same-named base parameters, per
    Helm's last-wins --set semantics — appended, not merged/deduped."""
    from argocheck.models import HelmParameter

    base_node = _base_node(parameters=[
        HelmParameter("cluster", "should-be-overridden"),
        HelmParameter("unrelated", "kept"),
    ])
    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-a")
    node = build_leaf_node(base_node, leaf)

    names_in_order = [p.name for p in node.sources[0].parameters]
    values = {p.name: p.value for p in node.sources[0].parameters}

    assert names_in_order == ["cluster", "unrelated", "cluster", "namespace", "sourceRepo"]
    # Helm applies --set flags in order and the later one wins, so the
    # tree-supplied "cluster" (appended last among the duplicates) takes effect.
    assert values["cluster"] == "prod"


def test_build_leaf_node_only_touches_first_source():
    """A multi-source base app's other sources (e.g. a $ref values source)
    must be carried over unchanged — only the first source gets tree params."""
    from argocheck.models import HelmParameter

    ref_source = HelmSource(repo_url="./values-repo", ref="vals")
    chart_source = HelmSource(repo_url="./root-chart", parameters=[HelmParameter("base", "1")])
    base_node = AppNode(name="my-app", namespace="argocd", sources=[chart_source, ref_source])

    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-a")
    node = build_leaf_node(base_node, leaf)

    assert len(node.sources) == 2
    assert node.sources[1] is ref_source  # untouched
    # Release name stays whatever the un-fanned base app would use (falls
    # back to base_node.name here, since chart_source has none set) — the
    # per-leaf tree label goes on node.name, not the Helm release name, so
    # rendered resource names don't diverge between leaves.
    assert node.sources[0].release_name == "my-app"
    assert node.name == "my-app (prod/ns-a)"
    param_names = [p.name for p in node.sources[0].parameters]
    assert param_names == ["base", "cluster", "namespace", "sourceRepo"]


def test_missing_argocheck_root_raises():
    doc = _doc()
    del doc["argocheck_root"]
    with pytest.raises(ValueTreeError, match="argocheck_root"):
        parse_leaves(doc)


def test_argocheck_root_pointing_at_missing_key_raises():
    doc = _doc(argocheck_root="does-not-exist")
    with pytest.raises(ValueTreeError, match="does-not-exist"):
        parse_leaves(doc)


def test_variable_mappings_length_mismatch_raises():
    doc = _doc(argocheck_variable_mappings=["", "cluster"])  # missing "namespace"
    with pytest.raises(ValueTreeError, match="leaf_depth"):
        parse_leaves(doc)


def test_leaf_depth_must_be_positive():
    doc = _doc(argocheck_leaf_depth=0, argocheck_variable_mappings=[""])
    with pytest.raises(ValueTreeError, match="positive"):
        parse_leaves(doc)


def test_non_mapping_at_tree_level_raises():
    doc = _doc()
    doc["clusters"]["prod"] = "not-a-mapping"
    with pytest.raises(ValueTreeError, match="mapping"):
        parse_leaves(doc)


def test_duplicate_release_names_raise():
    """Two distinct paths can still hyphenate to the same release name
    (cluster="a-b",namespace="c" vs cluster="a",namespace="b-c") — must be
    rejected rather than silently clobbering one leaf's render."""
    doc = _doc()
    doc["clusters"] = {
        "a-b": {"c": {"sourceRepo": "x"}},
        "a": {"b-c": {"sourceRepo": "y"}},
    }
    with pytest.raises(ValueTreeError, match="Duplicate"):
        parse_leaves(doc)


def test_matches_selection_prefix_and_exact():
    leaves = parse_leaves(_doc())
    prod_ns_a = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-a")
    qa_ns_a = next(leaf for leaf in leaves if leaf.display_path == "qa/ns-a")

    assert matches_selection(prod_ns_a, "prod")
    assert matches_selection(prod_ns_a, "prod/ns-a")
    assert not matches_selection(prod_ns_a, "prod/ns-b")
    assert not matches_selection(qa_ns_a, "prod")


def test_build_leaf_node_uses_base_namespace_and_preserves_release_name():
    """node.name shows "<base app name> (<leaf path>)" (for argocheck's own
    tree/diff UI, since the base app's own name would otherwise be invisible
    once fanned out); the Helm release name is pinned to whatever the
    un-fanned base app would use, so every leaf renders identical resource
    names/content unless the user's own tree values actually cause a
    difference."""
    base_node = _base_node()
    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "qa/ns-a")

    node = build_leaf_node(base_node, leaf)

    assert node.name == "my-app (qa/ns-a)"
    assert node.namespace == "argocd"  # carried over from base_node
    assert node.sources[0].release_name == "my-app"  # unchanged from base_node.name
    assert node.sources[0].repo_url == base_node.sources[0].repo_url


def test_build_leaf_node_preserves_explicit_base_release_name():
    """If the base app declares its own helm.releaseName, that (not
    base_node.name) is what every leaf must keep using."""
    base_node = _base_node(release_name="explicit-release")
    leaves = parse_leaves(_doc())
    leaf = next(leaf for leaf in leaves if leaf.display_path == "prod/ns-b")

    node = build_leaf_node(base_node, leaf)

    assert node.name == "my-app (prod/ns-b)"
    assert node.sources[0].release_name == "explicit-release"
