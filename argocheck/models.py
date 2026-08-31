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
    ref: str | None = None            # multi-source: alias for $ref resolution in valueFiles
    chart: str | None = None          # set for Helm chart repos (OCI/HTTP)
    path: str | None = None           # set for Git repos with a chart directory
    target_revision: str = "HEAD"
    release_name: str | None = None
    values: str | None = None         # inline values YAML string
    values_object: dict[str, Any] | None = None
    value_files: list[str] = field(default_factory=list)
    ignore_missing_value_files: bool = False
    parameters: list[HelmParameter] = field(default_factory=list)
    version: str | None = None        # helm API version hint

    @property
    def is_ref_only(self) -> bool:
        """True when this source only provides values files (not a chart to render)."""
        return bool(self.ref) and not self.chart and not self.path


@dataclass
class AppNode:
    name: str
    namespace: str
    # Always at least one entry; multi-source apps have more than one.
    sources: list[HelmSource]
    children: list[AppNode] = field(default_factory=list)
    # Non-Application resources rendered by this app's helm template(s)
    manifests: list[dict[str, Any]] = field(default_factory=list)
    # The raw Application manifest dict this node was parsed from (None for root)
    app_manifest: dict[str, Any] | None = None
    # Resolved chart directory of the primary (first) chart source
    chart_dir: Path | None = None
    error: Exception | None = None

    @property
    def source(self) -> HelmSource:
        """Convenience accessor — returns the first source."""
        return self.sources[0]

    @property
    def is_multi_source(self) -> bool:
        return len(self.sources) > 1
