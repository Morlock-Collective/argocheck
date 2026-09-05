"""Parse and enumerate "value tree" (environment map) specs.

A value tree is an OPTIONAL add-on to a normal root Application/chart: it
fans that same root out across a nested value map (e.g. cluster ->
namespace), turning each leaf of the map into its own AppNode cloned from
the root, with the tree path and the leaf's own key/value pairs passed in as
extra Helm --set parameters on the root's first source. The root app itself
is parsed exactly as it always is (parse_application / a chart directory) —
a value tree only ever describes the fan-out, never the chart reference.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from .models import AppNode, HelmParameter

_ROOT_KEY = "argocheck_root"
_LEAF_DEPTH_KEY = "argocheck_leaf_depth"
_VARIABLE_MAPPINGS_KEY = "argocheck_variable_mappings"


class ValueTreeError(Exception):
    pass


@dataclass
class ValueTreeLeaf:
    path: tuple[str, ...]
    parameters: list[HelmParameter]

    @property
    def release_name(self) -> str:
        """A Helm/Kubernetes-safe name derived from the tree path."""
        return "-".join(self.path)

    @property
    def display_path(self) -> str:
        return "/".join(self.path)


def is_value_tree(doc: dict[str, Any]) -> bool:
    return _ROOT_KEY in doc


def parse_leaves(doc: dict[str, Any]) -> list[ValueTreeLeaf]:
    """Parse a value-tree document into its flattened list of leaves."""
    if _ROOT_KEY not in doc:
        raise ValueTreeError(f"Missing required key: {_ROOT_KEY}")
    root_key = doc[_ROOT_KEY]
    if not isinstance(root_key, str) or root_key not in doc:
        raise ValueTreeError(f"{_ROOT_KEY}={root_key!r} but no top-level {root_key!r} key found")

    leaf_depth = doc.get(_LEAF_DEPTH_KEY)
    if not isinstance(leaf_depth, int) or isinstance(leaf_depth, bool) or leaf_depth < 1:
        raise ValueTreeError(f"{_LEAF_DEPTH_KEY} must be a positive integer")

    variable_mappings = doc.get(_VARIABLE_MAPPINGS_KEY)
    if not isinstance(variable_mappings, list):
        raise ValueTreeError(f"{_VARIABLE_MAPPINGS_KEY} must be a list")
    if len(variable_mappings) != leaf_depth + 1:
        raise ValueTreeError(
            f"{_VARIABLE_MAPPINGS_KEY} must have exactly {_LEAF_DEPTH_KEY} + 1 "
            f"({leaf_depth + 1}) entries, got {len(variable_mappings)}"
        )
    for i, name in enumerate(variable_mappings):
        if not isinstance(name, str):
            raise ValueTreeError(f"{_VARIABLE_MAPPINGS_KEY}[{i}] must be a string")

    leaves: list[ValueTreeLeaf] = []
    _walk(doc[root_key], depth=1, leaf_depth=leaf_depth,
          variable_mappings=variable_mappings, path=(), params=[], out=leaves)

    if not leaves:
        raise ValueTreeError("Value tree produced no leaves")

    seen_names = set()
    for leaf in leaves:
        if leaf.release_name in seen_names:
            raise ValueTreeError(f"Duplicate leaf path/name: {leaf.display_path!r}")
        seen_names.add(leaf.release_name)

    return leaves


def _encode_leaf_value(value: Any) -> tuple[str, bool]:
    """Encode one leaf key's value for a Helm parameter. Returns (value, is_json).

    Lists and mappings are JSON-encoded and applied via --set-json, the only
    --set-family flag that can carry a structured value at all. Note this is
    *not* the highest-precedence flag Helm has — empirically (confirmed
    against a real `helm template`), Helm's fixed precedence order is
    --set-literal > --set-string > --set > --set-json > -f files, regardless
    of the order flags are given on the command line. So a leaf's structured
    value here will always beat any values file (including the chart's own
    values.yaml), but would lose to a same-key plain --set/--set-string
    parameter declared elsewhere on the same source — an inherent Helm
    limitation, not something any flag choice or ordering can work around.

    Scalars stay on plain --set (highest precedence achievable for a leaf
    value that might collide with a same-key base-app parameter), just
    correctly cased for bool/None — Python's str(True)/str(None) ("True"/
    "None") would otherwise be read back by Helm as those literal *strings*,
    not the boolean/null they're meant to be.
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value), True
    if isinstance(value, bool):
        return ("true" if value else "false"), False
    if value is None:
        return "null", False
    return str(value), False


def _walk(
    node: Any,
    depth: int,
    leaf_depth: int,
    variable_mappings: list[str],
    path: tuple[str, ...],
    params: list[HelmParameter],
    out: list[ValueTreeLeaf],
) -> None:
    if not isinstance(node, dict):
        where = "/".join(path) or "<root>"
        raise ValueTreeError(f"Expected a mapping at {where!r}, got {type(node).__name__}")

    if depth > leaf_depth:
        leaf_params = list(params)
        for key, value in node.items():
            encoded, is_json = _encode_leaf_value(value)
            leaf_params.append(HelmParameter(str(key), encoded, is_json=is_json))
        out.append(ValueTreeLeaf(path=path, parameters=leaf_params))
        return

    var_name = variable_mappings[depth]
    for key, child in node.items():
        child_params = list(params)
        if var_name:
            child_params.append(HelmParameter(var_name, str(key)))
        _walk(child, depth + 1, leaf_depth, variable_mappings,
              path + (str(key),), child_params, out)


def build_leaf_node(base_node: AppNode, leaf: ValueTreeLeaf) -> AppNode:
    """Clone base_node (the normally-parsed root Application/chart) for one
    leaf: the leaf's tree-path variables and own key/value pairs are appended
    to the *first* source's parameters, so they take priority via Helm's
    last-wins --set semantics. Only the first source is touched — a
    multi-source root app's other sources (e.g. $ref value sources) are
    carried over unchanged. Child Applications the leaf renders resolve
    their own parameters independently, unaffected by the fan-out.

    This AppNode's `name` is "<base app name> (<leaf display path>)" (e.g.
    "foo (prod/ns-a)") — the base app's own name is otherwise invisible once
    fanned out across many leaves, so it's shown alongside the path that
    actually distinguishes them, rather than the path alone. The Helm release
    name is explicitly pinned to whatever the base app would have used
    un-fanned — otherwise walker._release_name()'s `source.release_name or
    node.name` fallback would pick up this per-leaf node name and silently
    make every leaf's *rendered resources* diverge (via `{{ .Release.Name }}`)
    even when nothing the user specified was meant to change them."""
    sources = list(base_node.sources)
    first = sources[0]
    original_release_name = first.release_name or base_node.name
    sources[0] = replace(
        first,
        release_name=original_release_name,
        parameters=[*first.parameters, *leaf.parameters],
    )
    return AppNode(
        name=f"{base_node.name} ({leaf.display_path})",
        namespace=base_node.namespace,
        sources=sources,
        app_manifest=base_node.app_manifest,
    )


def matches_selection(leaf: ValueTreeLeaf, selection: str) -> bool:
    """True if `selection` names this leaf exactly, or a prefix of its path
    (e.g. selecting "clustername1" matches every namespace under it)."""
    selected_parts = tuple(selection.split("/"))
    return leaf.path[: len(selected_parts)] == selected_parts
