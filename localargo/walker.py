"""Recursive app-of-apps tree builder."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .helm import HelmError, run_template
from .models import AppNode, HelmSource
from .parser import ParseError, parse_application
from .resolver import ResolveError, resolve_ref_map, resolve_source


def _release_name(node: AppNode, source: HelmSource) -> str:
    return source.release_name or node.name


def walk(
    node: AppNode,
    tmp_dir: Path,
    *,
    argocd_env: bool = False,
    max_depth: int = 10,
    _depth: int = 0,
    _visited: set[str] | None = None,
    _parent_chart_dir: Path | None = None,
) -> AppNode:
    """Recursively resolve, render, and populate an AppNode tree."""
    if _visited is None:
        _visited = set()

    if _depth >= max_depth:
        node.error = RuntimeError(
            f"Max recursion depth ({max_depth}) reached at application {node.name!r}"
        )
        return node

    if node.name in _visited:
        node.error = RuntimeError(
            f"Cycle detected: application {node.name!r} has already been processed"
        )
        return node

    _visited = _visited | {node.name}

    # Build the ref map from any sources that carry a `ref` field.
    # These are value-only sources — they don't render a chart themselves.
    try:
        ref_map = resolve_ref_map(
            node.sources, tmp_dir=tmp_dir, working_dir=_parent_chart_dir
        )
    except (ResolveError, HelmError) as e:
        node.error = e
        return node

    # Identify the chart sources (sources without a ref, or with both ref and a chart/path).
    chart_sources = [s for s in node.sources if not s.is_ref_only]

    if not chart_sources:
        node.error = RuntimeError(
            f"Application {node.name!r}: no renderable chart source found "
            f"(all sources are ref-only)"
        )
        return node

    # Render each chart source, combining their output onto this node.
    all_docs: list[dict[str, Any]] = []
    primary_chart_dir: Path | None = None

    for source in chart_sources:
        try:
            chart_dir = resolve_source(
                source, tmp_dir=tmp_dir, working_dir=_parent_chart_dir
            )
        except (ResolveError, HelmError) as e:
            node.error = e
            return node

        if primary_chart_dir is None:
            primary_chart_dir = chart_dir

        try:
            docs = run_template(
                chart_path=chart_dir,
                source=source,
                release_name=_release_name(node, source),
                namespace=node.namespace,
                tmp_dir=tmp_dir,
                argocd_env=argocd_env,
                ref_map=ref_map,
            )
        except HelmError as e:
            node.error = e
            return node

        all_docs.extend(docs)

    node.chart_dir = primary_chart_dir

    # Split Application manifests from regular resources
    child_docs: list[dict[str, Any]] = []
    for doc in all_docs:
        if doc.get("kind") == "Application" and doc.get("apiVersion", "").startswith("argoproj.io"):
            child_docs.append(doc)
        else:
            node.manifests.append(doc)

    # Recurse into child Applications
    for child_doc in child_docs:
        try:
            child_node = parse_application(child_doc)
        except ParseError as e:
            dummy = AppNode(
                name=child_doc.get("metadata", {}).get("name", "<unknown>"),
                namespace=child_doc.get("metadata", {}).get("namespace", "default"),
                sources=[HelmSource(repo_url="")],
                error=e,
            )
            node.children.append(dummy)
            continue

        child_node = walk(
            child_node,
            tmp_dir=tmp_dir,
            argocd_env=argocd_env,
            max_depth=max_depth,
            _depth=_depth + 1,
            _visited=_visited,
            _parent_chart_dir=primary_chart_dir,
        )
        node.children.append(child_node)

    return node
