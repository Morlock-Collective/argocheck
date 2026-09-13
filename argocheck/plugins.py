"""Plugin discovery and hook points for extending argocheck.

An organization can depend on argocheck from its own Python package (e.g.
"spotify-argocheck") and tailor it to its own chart/app-structure conventions
without forking argocheck itself: a plugin subclasses ArgocheckPlugin,
overrides only the hooks it needs, and registers itself under the
"argocheck.plugins" entry-point group in its own pyproject.toml. See
EXTENDING.md for the full guide and a worked example.

Every hook defaults to a no-op / passthrough, so installing zero plugins
leaves argocheck's behavior completely unchanged — this module adds no new
required configuration, and every call site that consults get_registry()
degrades to today's behavior with an empty registry.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .models import AppNode, HelmSource

_ENTRY_POINT_GROUP = "argocheck.plugins"


@dataclass
class ResolveContext:
    """Context passed to ArgocheckPlugin.resolve_source, mirroring the
    parameters resolver.resolve_source() itself takes."""
    working_dir: Path | None
    tmp_dir: Path
    require_chart: bool


@dataclass
class HelmContext:
    """Context passed to ArgocheckPlugin.build_helm_command, mirroring the
    parameters helm.build_template_cmd() itself takes."""
    chart_path: Path
    release_name: str
    namespace: str
    tmp_dir: Path
    argocd_env: bool


class ArgocheckPlugin:
    """Base class for an argocheck plugin. Override only the hooks you need —
    every default here is a no-op/passthrough."""

    #: A short human-readable name, used in warnings if this plugin misbehaves.
    name: str = "unnamed-plugin"

    def transform_application(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Called with the raw Application manifest dict, before argocheck
        parses it — for both the root app and every child Application
        discovered while walking the tree. Return a (possibly modified) dict;
        the default returns doc unchanged."""
        return doc

    def resolve_source(self, source: "HelmSource", context: ResolveContext) -> Path | None:
        """Called before argocheck's own local/git/Helm-repo source
        resolution. Return a resolved chart/values directory to short-circuit
        argocheck's own resolution entirely (e.g. for a custom repoURL
        scheme), or None (the default) to fall through to it unchanged."""
        return None

    def build_helm_command(
        self, cmd: list[str], source: "HelmSource", context: HelmContext
    ) -> list[str]:
        """Called with the fully-built `helm template` argv, just before it
        runs. Return a (possibly modified) list; the default returns cmd
        unchanged."""
        return cmd

    def after_walk(self, node: "AppNode") -> None:
        """Called once per AppNode, after it (and its children) have been
        fully resolved and rendered. Attach data via node.extra — this hook's
        return value is ignored."""
        return None

    def register_routes(self, app: "FastAPI") -> None:
        """Called once when the web server starts. Mount extra routes/static
        files on `app` directly (e.g. app.include_router(...), app.mount(...))."""
        return None

    def help_topics(self) -> list[dict[str, Any]]:
        """Extra guide topics, in the same {id, title, blocks} shape as
        help_content.HELP_TOPICS, merged into both the CLI guide and the web
        interface's help modal."""
        return []

    def frontend_assets(self) -> list[Path]:
        """Extra JS files served under a per-plugin static path and injected
        as <script> tags into the web interface's index.html, in
        registration order."""
        return []


class PluginRegistry:
    """Holds the loaded plugins and folds each hook across all of them. This
    is the only thing core argocheck modules call — with zero plugins loaded,
    every method here is a pure passthrough."""

    def __init__(self, plugins: list[ArgocheckPlugin] | None = None):
        self.plugins = plugins or []

    def transform_application(self, doc: dict[str, Any]) -> dict[str, Any]:
        for plugin in self.plugins:
            doc = _guarded(plugin, "transform_application", doc, default=doc)
        return doc

    def resolve_source(self, source: "HelmSource", context: ResolveContext) -> Path | None:
        for plugin in self.plugins:
            result = _guarded(plugin, "resolve_source", source, context, default=None)
            if result is not None:
                return result
        return None

    def build_helm_command(
        self, cmd: list[str], source: "HelmSource", context: HelmContext
    ) -> list[str]:
        for plugin in self.plugins:
            cmd = _guarded(plugin, "build_helm_command", cmd, source, context, default=cmd)
        return cmd

    def after_walk(self, node: "AppNode") -> None:
        for plugin in self.plugins:
            _guarded(plugin, "after_walk", node, default=None)

    def register_routes(self, app: "FastAPI") -> None:
        for plugin in self.plugins:
            _guarded(plugin, "register_routes", app, default=None)

    def help_topics(self) -> list[dict[str, Any]]:
        topics: list[dict[str, Any]] = []
        for plugin in self.plugins:
            topics.extend(_guarded(plugin, "help_topics", default=[]))
        return topics

    def frontend_assets(self) -> list[tuple[str, Path]]:
        """Returns (plugin_name, asset_path) pairs, in registration order."""
        assets: list[tuple[str, Path]] = []
        for plugin in self.plugins:
            for path in _guarded(plugin, "frontend_assets", default=[]):
                assets.append((plugin.name, path))
        return assets


def _guarded(plugin: ArgocheckPlugin, method_name: str, *args: Any, default: Any) -> Any:
    """Call one plugin hook, isolated from the rest of argocheck: a plugin
    that raises (or was never fully constructed) logs a warning to stderr and
    is skipped for this call, rather than taking argocheck down with it."""
    try:
        return getattr(plugin, method_name)(*args)
    except Exception as e:  # noqa: BLE001 - a plugin's own bug must not propagate
        print(
            f"argocheck: plugin {plugin.name!r} raised in {method_name}(): {e}",
            file=sys.stderr,
        )
        return default


@lru_cache(maxsize=1)
def get_registry() -> PluginRegistry:
    """The process-wide plugin registry, discovered once and cached. Call
    load_plugins() directly (and pass the result explicitly) if you need a
    fresh or test-specific registry instead."""
    return load_plugins()


def load_plugins(group: str = _ENTRY_POINT_GROUP) -> PluginRegistry:
    """Discover and instantiate every plugin registered under `group`. An
    entry point may resolve to an ArgocheckPlugin subclass (instantiated with
    no arguments) or a zero-argument factory function returning an instance.
    A plugin that fails to load or construct is skipped with a stderr
    warning — never allowed to prevent argocheck from starting."""
    plugins: list[ArgocheckPlugin] = []
    for ep in entry_points(group=group):
        try:
            obj = ep.load()
            # An entry point may point at an ArgocheckPlugin subclass or a
            # zero-arg factory function — both are called to get an instance.
            # It may also point directly at an already-built instance, which
            # is used as-is.
            plugin = obj if isinstance(obj, ArgocheckPlugin) else obj()
        except Exception as e:  # noqa: BLE001 - a broken plugin must not block startup
            print(f"argocheck: failed to load plugin {ep.name!r}: {e}", file=sys.stderr)
            continue
        plugins.append(plugin)
    return PluginRegistry(plugins)
