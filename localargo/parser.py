"""Parse ArgoCD Application manifests into AppNode instances."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import AppNode, HelmParameter, HelmSource


class ParseError(Exception):
    pass


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text()
    except OSError as e:
        raise ParseError(f"Cannot read {path}: {e}") from e

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ParseError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(doc, dict):
        raise ParseError(f"{path} does not contain a YAML mapping")
    return doc


def parse_application(doc: dict[str, Any]) -> AppNode:
    """Parse a raw Application manifest dict into an AppNode."""
    kind = doc.get("kind", "")
    if kind != "Application":
        raise ParseError(f"Expected kind: Application, got: {kind!r}")

    metadata = doc.get("metadata") or {}
    name = metadata.get("name") or ""
    namespace = metadata.get("namespace") or "default"

    if not name:
        raise ParseError("Application has no metadata.name")

    spec = doc.get("spec") or {}

    # Multi-source: spec.sources (list)
    if "sources" in spec:
        raw_sources = spec["sources"] or []
        if not raw_sources:
            raise ParseError(f"Application {name!r}: spec.sources is empty")
        sources = [_parse_helm_source(name, s) for s in raw_sources]
        return AppNode(name=name, namespace=namespace, sources=sources, app_manifest=doc)

    # Single source: spec.source
    raw_source = spec.get("source")
    if not raw_source:
        raise ParseError(f"Application {name!r} has no spec.source or spec.sources")

    source = _parse_helm_source(name, raw_source)
    return AppNode(name=name, namespace=namespace, sources=[source], app_manifest=doc)


def _parse_helm_source(app_name: str, raw: dict[str, Any]) -> HelmSource:
    repo_url = raw.get("repoURL") or ""
    if not repo_url:
        raise ParseError(f"Application {app_name!r}: spec.source.repoURL is required")

    ref = raw.get("ref") or None
    chart = raw.get("chart") or None
    path = raw.get("path") or None
    target_revision = raw.get("targetRevision") or "HEAD"

    helm_raw = raw.get("helm") or {}
    release_name = helm_raw.get("releaseName") or None

    values = helm_raw.get("values") or None
    values_object = helm_raw.get("valuesObject") or None

    value_files: list[str] = helm_raw.get("valueFiles") or []
    ignore_missing_value_files = bool(helm_raw.get("ignoreMissingValueFiles", False))

    raw_params: list[dict[str, Any]] = helm_raw.get("parameters") or []
    parameters = [
        HelmParameter(
            name=p["name"],
            value=str(p.get("value", "")),
            force_string=bool(p.get("forceString", False)),
        )
        for p in raw_params
        if "name" in p
    ]

    version = helm_raw.get("version") or None

    return HelmSource(
        repo_url=repo_url,
        ref=ref,
        chart=chart,
        path=path,
        target_revision=target_revision,
        release_name=release_name,
        values=values,
        values_object=values_object,
        value_files=value_files,
        ignore_missing_value_files=ignore_missing_value_files,
        parameters=parameters,
        version=version,
    )


def parse_all_applications(yaml_text: str) -> list[AppNode]:
    """Parse all Application documents from a multi-doc YAML string."""
    apps: list[AppNode] = []
    for doc in yaml.safe_load_all(yaml_text):
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") == "Application":
            apps.append(parse_application(doc))
    return apps
