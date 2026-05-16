from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HelmParameter:
    name: str
    value: str
    force_string: bool = False


@dataclass
class HelmSource:
    repo_url: str
    chart: str | None = None          # set for Helm chart repos (OCI/HTTP)
    path: str | None = None           # set for Git repos with a chart directory
    target_revision: str = "HEAD"
    release_name: str | None = None
    values: str | None = None         # inline values YAML string
    values_object: dict[str, Any] | None = None
    value_files: list[str] = field(default_factory=list)
    parameters: list[HelmParameter] = field(default_factory=list)
    version: str | None = None        # helm API version hint


@dataclass
class AppNode:
    name: str
    namespace: str
    source: HelmSource
    children: list[AppNode] = field(default_factory=list)
    # Non-Application resources rendered by this app's helm template
    manifests: list[dict[str, Any]] = field(default_factory=list)
    # Absolute path to the resolved chart directory (set during processing)
    chart_dir: Path | None = None
    error: Exception | None = None
