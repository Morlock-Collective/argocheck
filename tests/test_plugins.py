"""Tests for the plugin discovery/hook system (argocheck.plugins).

Two layers of coverage, deliberately kept separate:

- "Unit" tests exercise PluginRegistry directly with fake plugins — cheap,
  and pin down the folding/isolation semantics precisely (chain order,
  first-non-None-wins, a raising plugin never taking the caller down).
- "Integration" tests exercise the *real* argocheck functions (resolver,
  helm, parser, walker, server, cli) with a plugin installed via
  monkeypatching that module's own `get_registry` — proving each hook is
  actually wired into the call site the docs/EXTENDING.md claim it is, not
  just that PluginRegistry itself folds correctly in isolation. A hook that
  got silently disconnected from its call site (e.g. during a refactor)
  would still pass every unit test here; only the integration tests catch it.
"""
import importlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from argocheck.models import AppNode, HelmParameter, HelmSource
from argocheck.parser import load_yaml_file, parse_application
from argocheck.plugins import (
    ArgocheckPlugin,
    HelmContext,
    PluginRegistry,
    ResolveContext,
    load_plugins,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Unit: PluginRegistry in isolation ───────────────────────────────────────

def test_empty_registry_is_a_pure_passthrough():
    registry = PluginRegistry([])
    doc = {"kind": "Application"}
    context = ResolveContext(working_dir=None, tmp_dir=Path("/tmp"), require_chart=True)

    assert registry.transform_application(doc) is doc
    assert registry.resolve_source(object(), context) is None
    assert registry.build_helm_command(["helm"], object(), None) == ["helm"]
    assert registry.after_walk(object()) is None
    assert registry.help_topics() == []
    assert registry.frontend_assets() == []


def test_transform_application_chains_through_every_plugin():
    class AddFoo(ArgocheckPlugin):
        def transform_application(self, doc):
            return {**doc, "foo": True}

    class AddBar(ArgocheckPlugin):
        def transform_application(self, doc):
            return {**doc, "bar": True}

    registry = PluginRegistry([AddFoo(), AddBar()])
    result = registry.transform_application({"kind": "Application"})
    assert result == {"kind": "Application", "foo": True, "bar": True}


def test_resolve_source_first_non_none_wins():
    context = ResolveContext(working_dir=None, tmp_dir=Path("/tmp"), require_chart=True)

    class Abstains(ArgocheckPlugin):
        def resolve_source(self, source, context):
            return None

    class Resolves(ArgocheckPlugin):
        def resolve_source(self, source, context):
            return Path("/resolved/by/plugin")

    class NeverCalled(ArgocheckPlugin):
        def resolve_source(self, source, context):
            raise AssertionError("should not be reached")

    registry = PluginRegistry([Abstains(), Resolves(), NeverCalled()])
    assert registry.resolve_source(object(), context) == Path("/resolved/by/plugin")


def test_build_helm_command_chains_through_every_plugin():
    class AddSet(ArgocheckPlugin):
        def build_helm_command(self, cmd, source, context):
            return [*cmd, "--set", "org=acme"]

    registry = PluginRegistry([AddSet()])
    context = HelmContext(
        chart_path=Path("."), release_name="r", namespace="ns",
        tmp_dir=Path("/tmp"), argocd_env=False,
    )
    assert registry.build_helm_command(["helm", "template"], object(), context) == [
        "helm", "template", "--set", "org=acme",
    ]


def test_after_walk_calls_every_plugin():
    calls = []

    class Recorder(ArgocheckPlugin):
        def after_walk(self, node):
            calls.append(node)

    registry = PluginRegistry([Recorder(), Recorder()])
    sentinel = object()
    registry.after_walk(sentinel)
    assert calls == [sentinel, sentinel]


def test_help_topics_and_frontend_assets_concatenate():
    class PluginA(ArgocheckPlugin):
        name = "plugin-a"

        def help_topics(self):
            return [{"id": "a", "title": "A", "blocks": []}]

        def frontend_assets(self):
            return [Path("/a/main.js")]

    class PluginB(ArgocheckPlugin):
        name = "plugin-b"

        def help_topics(self):
            return [{"id": "b", "title": "B", "blocks": []}]

        def frontend_assets(self):
            return [Path("/b/main.js")]

    registry = PluginRegistry([PluginA(), PluginB()])
    assert [t["id"] for t in registry.help_topics()] == ["a", "b"]
    assert registry.frontend_assets() == [
        ("plugin-a", Path("/a/main.js")),
        ("plugin-b", Path("/b/main.js")),
    ]


def test_a_raising_plugin_is_skipped_not_fatal(capsys):
    class Broken(ArgocheckPlugin):
        name = "broken-plugin"

        def transform_application(self, doc):
            raise RuntimeError("boom")

    class Fine(ArgocheckPlugin):
        def transform_application(self, doc):
            return {**doc, "fine": True}

    registry = PluginRegistry([Broken(), Fine()])
    doc = {"kind": "Application"}
    result = registry.transform_application(doc)

    # Broken's contribution is dropped (doc passed through unchanged for it),
    # but Fine still runs, and nothing propagates as an exception.
    assert result == {"kind": "Application", "fine": True}
    assert "broken-plugin" in capsys.readouterr().err


def test_load_plugins_instantiates_class_and_factory_entry_points():
    class FromClass(ArgocheckPlugin):
        name = "from-class"

    def make_plugin():
        p = ArgocheckPlugin()
        p.name = "from-factory"
        return p

    class FakeEntryPoint:
        def __init__(self, name, obj):
            self.name = name
            self._obj = obj

        def load(self):
            return self._obj

    fake_eps = [FakeEntryPoint("a", FromClass), FakeEntryPoint("b", make_plugin)]

    with patch("argocheck.plugins.entry_points", return_value=fake_eps):
        registry = load_plugins()

    assert {p.name for p in registry.plugins} == {"from-class", "from-factory"}


def test_load_plugins_accepts_an_already_built_instance():
    instance = ArgocheckPlugin()
    instance.name = "already-built"

    class FakeEntryPoint:
        name = "x"

        def load(self):
            return instance

    with patch("argocheck.plugins.entry_points", return_value=[FakeEntryPoint()]):
        registry = load_plugins()

    assert registry.plugins == [instance]


def test_load_plugins_skips_a_broken_entry_point(capsys):
    class FakeEntryPoint:
        name = "broken"

        def load(self):
            raise ImportError("nope")

    with patch("argocheck.plugins.entry_points", return_value=[FakeEntryPoint()]):
        registry = load_plugins()

    assert registry.plugins == []
    assert "broken" in capsys.readouterr().err


# ── Integration: resolver.resolve_source() ──────────────────────────────────

def test_resolve_source_real_integration_short_circuits(monkeypatch):
    """A plugin recognizing its own repoURL scheme bypasses argocheck's own
    local/git/Helm-repo dispatch entirely — the scenario EXTENDING.md's
    worked example is built around."""
    class SchemePlugin(ArgocheckPlugin):
        def resolve_source(self, source, context):
            if source.repo_url.startswith("myorg://"):
                return FIXTURES / "simple-chart"
            return None

    monkeypatch.setattr("argocheck.resolver.get_registry", lambda: PluginRegistry([SchemePlugin()]))

    from argocheck.resolver import resolve_source

    src = HelmSource(repo_url="myorg://whatever-this-means-internally")
    with tempfile.TemporaryDirectory() as tmp:
        result = resolve_source(src, tmp_dir=Path(tmp))

    assert result == FIXTURES / "simple-chart"


def test_resolve_source_real_integration_abstaining_plugin_falls_through(monkeypatch):
    """A plugin that returns None for a source it doesn't recognize must not
    block argocheck's own resolution from running normally."""
    class SchemePlugin(ArgocheckPlugin):
        def resolve_source(self, source, context):
            if source.repo_url.startswith("myorg://"):
                return Path("/should/not/be/used")
            return None

    monkeypatch.setattr("argocheck.resolver.get_registry", lambda: PluginRegistry([SchemePlugin()]))

    from argocheck.resolver import resolve_source

    src = HelmSource(repo_url=str(FIXTURES / "simple-chart"))
    with tempfile.TemporaryDirectory() as tmp:
        result = resolve_source(src, tmp_dir=Path(tmp))

    assert result == FIXTURES / "simple-chart"


# ── Integration: helm.build_template_cmd() ──────────────────────────────────

def test_build_template_cmd_real_integration(monkeypatch):
    class AddSetPlugin(ArgocheckPlugin):
        def build_helm_command(self, cmd, source, context):
            return [*cmd, "--set", "org=acme"]

    monkeypatch.setattr("argocheck.helm.get_registry", lambda: PluginRegistry([AddSetPlugin()]))

    from argocheck.helm import build_template_cmd

    source = HelmSource(repo_url=".")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = build_template_cmd(Path("."), source, "rel", "ns", Path(tmp))

    assert cmd[-2:] == ["--set", "org=acme"]


# ── Integration: parser.parse_application() ─────────────────────────────────

def test_parse_application_real_integration_transform(monkeypatch):
    """A plugin normalizing an org-specific manifest field into the shape
    argocheck already understands, before parsing proceeds."""
    class RenameFieldPlugin(ArgocheckPlugin):
        def transform_application(self, doc):
            if "myOrgRepo" not in doc.get("spec", {}):
                return doc
            doc = {**doc, "spec": {**doc["spec"]}}
            doc["spec"]["source"] = {"repoURL": doc["spec"].pop("myOrgRepo")}
            return doc

    monkeypatch.setattr("argocheck.parser.get_registry", lambda: PluginRegistry([RenameFieldPlugin()]))

    doc = {
        "kind": "Application",
        "metadata": {"name": "app"},
        "spec": {"myOrgRepo": "myorg://repo"},
    }
    node = parse_application(doc)

    assert node.sources[0].repo_url == "myorg://repo"


def test_parse_application_real_integration_runs_for_child_apps_too(monkeypatch):
    """transform_application must apply uniformly to every Application
    parse_application() ever sees — not just a root app's own call site —
    since child Applications discovered while walking funnel through the
    same function."""
    seen_names = []

    class Recorder(ArgocheckPlugin):
        def transform_application(self, doc):
            seen_names.append(doc.get("metadata", {}).get("name"))
            return doc

    monkeypatch.setattr("argocheck.parser.get_registry", lambda: PluginRegistry([Recorder()]))
    monkeypatch.setattr("argocheck.walker.get_registry", lambda: PluginRegistry([]))

    from argocheck.walker import walk

    doc = load_yaml_file(FIXTURES / "root-app-parent.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "parent-chart")

    with tempfile.TemporaryDirectory() as tmp:
        walk(node, tmp_dir=Path(tmp))

    # "root-app" was parsed before walk() even started (that call isn't
    # patched above, so it's not recorded); "child-app" is discovered and
    # parsed *during* the walk, which is the call this test is really after.
    assert "child-app" in seen_names


# ── Integration: walker.walk() ───────────────────────────────────────────────

def test_walk_real_integration_after_walk_runs_post_order(monkeypatch):
    order = []

    class OrderPlugin(ArgocheckPlugin):
        def after_walk(self, node):
            order.append(node.name)

    monkeypatch.setattr("argocheck.walker.get_registry", lambda: PluginRegistry([OrderPlugin()]))

    from argocheck.walker import walk

    doc = load_yaml_file(FIXTURES / "root-app-parent.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "parent-chart")

    with tempfile.TemporaryDirectory() as tmp:
        walk(node, tmp_dir=Path(tmp))

    # Post-order: the child is hooked before its parent.
    assert order == ["child-app", "root-app"]


def test_walk_real_integration_after_walk_runs_on_error_nodes_too(monkeypatch):
    """after_walk must fire even when walk() bails out early (cycle
    detection, max depth, a resolve/render failure) — a plugin inspecting
    every node it's handed should never have to special-case errors."""
    seen = []

    class Recorder(ArgocheckPlugin):
        def after_walk(self, node):
            seen.append((node.name, node.error is not None))

    monkeypatch.setattr("argocheck.walker.get_registry", lambda: PluginRegistry([Recorder()]))

    from argocheck.walker import walk

    src = HelmSource(repo_url=str(FIXTURES / "simple-chart"))
    node = AppNode(name="self-ref", namespace="default", sources=[src])

    with tempfile.TemporaryDirectory() as tmp:
        walk(node, tmp_dir=Path(tmp), _visited={"self-ref"})

    assert seen == [("self-ref", True)]


def test_after_walk_extra_survives_serialization(monkeypatch):
    """Data a plugin attaches via node.extra in after_walk must reach the
    frontend through the same JSON the web interface renders from."""
    class TagPlugin(ArgocheckPlugin):
        def after_walk(self, node):
            node.extra["reviewed"] = node.name

    monkeypatch.setattr("argocheck.walker.get_registry", lambda: PluginRegistry([TagPlugin()]))

    from argocheck.server import _ser_node
    from argocheck.walker import walk

    doc = load_yaml_file(FIXTURES / "root-app-plain.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "plain-manifests")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        result = walk(node, tmp_dir=tmp_dir)
        serialized = _ser_node(result, tmp_dir)

    assert serialized["extra"] == {"reviewed": "plain-app"}


# ── Integration: server.py (create_app / route wiring) ──────────────────────

def test_create_app_merges_plugin_help_topics():
    class HelpPlugin(ArgocheckPlugin):
        def help_topics(self):
            return [{"id": "myorg", "title": "MyOrg", "blocks": []}]

    from argocheck.server import create_app

    app = create_app(registry=PluginRegistry([HelpPlugin()]))
    client = TestClient(app)

    topics = client.get("/api/help").json()["topics"]
    assert any(t["id"] == "myorg" for t in topics)
    # The built-in topics are still there too — merged, not replaced.
    assert any(t["id"] == "basics" for t in topics)


def test_create_app_serves_plugin_frontend_assets_and_injects_script_tag(tmp_path):
    asset_dir = tmp_path / "static"
    asset_dir.mkdir()
    (asset_dir / "main.js").write_text("window.__myorgPluginLoaded = true;")

    class AssetPlugin(ArgocheckPlugin):
        name = "myorg"

        def frontend_assets(self):
            return [asset_dir / "main.js"]

    from argocheck.server import create_app

    app = create_app(registry=PluginRegistry([AssetPlugin()]))
    client = TestClient(app)

    index_html = client.get("/").text
    assert '<script src="/plugin-static/myorg/main.js"></script>' in index_html

    asset_response = client.get("/plugin-static/myorg/main.js")
    assert asset_response.status_code == 200
    assert "__myorgPluginLoaded" in asset_response.text


def test_create_app_lets_a_plugin_register_its_own_routes():
    class RoutePlugin(ArgocheckPlugin):
        def register_routes(self, app):
            @app.get("/api/myorg/status")
            def status():
                return {"ok": True}

    from argocheck.server import create_app

    app = create_app(registry=PluginRegistry([RoutePlugin()]))
    client = TestClient(app)

    assert client.get("/api/myorg/status").json() == {"ok": True}
    # Core routes are still there alongside it.
    assert client.get("/api/version").status_code == 200


def test_create_app_two_plugins_frontend_assets_never_collide():
    """Two plugins each shipping a main.js must not be mounted onto the same
    /plugin-static/ path — the mount is namespaced by plugin name."""
    import tempfile as _tempfile

    dir_a = Path(_tempfile.mkdtemp())
    dir_b = Path(_tempfile.mkdtemp())
    (dir_a / "main.js").write_text("/* a */")
    (dir_b / "main.js").write_text("/* b */")

    class PluginA(ArgocheckPlugin):
        name = "plugin-a"

        def frontend_assets(self):
            return [dir_a / "main.js"]

    class PluginB(ArgocheckPlugin):
        name = "plugin-b"

        def frontend_assets(self):
            return [dir_b / "main.js"]

    from argocheck.server import create_app

    app = create_app(registry=PluginRegistry([PluginA(), PluginB()]))
    client = TestClient(app)

    assert client.get("/plugin-static/plugin-a/main.js").text == "/* a */"
    assert client.get("/plugin-static/plugin-b/main.js").text == "/* b */"


def test_create_app_with_no_registry_argument_uses_the_process_wide_one():
    """create_app() with no explicit registry must not silently run with
    zero plugins — it has to fall back to get_registry()."""
    from argocheck import server as server_module

    class HelpPlugin(ArgocheckPlugin):
        def help_topics(self):
            return [{"id": "myorg", "title": "MyOrg", "blocks": []}]

    with patch.object(server_module, "get_registry", return_value=PluginRegistry([HelpPlugin()])):
        app = server_module.create_app()

    client = TestClient(app)
    topics = client.get("/api/help").json()["topics"]
    assert any(t["id"] == "myorg" for t in topics)


# ── Integration: cli.py (guide topics baked in at import time) ─────────────

def test_cli_guide_topics_include_plugin_topics(monkeypatch):
    """cli.py computes its guide topic list once, at import time
    (_ALL_GUIDE_TOPICS = HELP_TOPICS + get_registry().help_topics()) — a
    real, easy-to-break wiring point a pure PluginRegistry unit test can't
    see at all. Reload the module with a patched plugin set to prove it."""
    class GuidePlugin(ArgocheckPlugin):
        name = "myorg"

        def help_topics(self):
            return [{"id": "myorg", "title": "MyOrg", "blocks": []}]

    class FakeEntryPoint:
        name = "myorg"

        def load(self):
            return GuidePlugin

    import argocheck.cli as cli_module
    import argocheck.plugins as plugins_module

    monkeypatch.setattr(plugins_module, "entry_points", lambda group=None: [FakeEntryPoint()])
    plugins_module.get_registry.cache_clear()

    try:
        importlib.reload(cli_module)
        assert "myorg" in cli_module._GUIDE_TOPIC_IDS
        assert any(t["id"] == "myorg" for t in cli_module._ALL_GUIDE_TOPICS)
    finally:
        # Undo the monkeypatch *now* (not at test teardown) so this reload,
        # still inside the test, rebuilds cli_module against the real
        # (plugin-free, in this venv) world — leaving no stale plugin
        # topics baked into the module for whatever runs after this test.
        monkeypatch.undo()
        plugins_module.get_registry.cache_clear()
        importlib.reload(cli_module)


# ── End-to-end: multiple hooks cooperating on one real render ──────────────

def test_multiple_hooks_cooperate_on_one_real_walk(monkeypatch):
    """A single plugin using transform_application + resolve_source +
    build_helm_command + after_walk together, mirroring EXTENDING.md's
    worked example, through one real walk() call (real helm invocation)."""
    class MyOrgPlugin(ArgocheckPlugin):
        name = "myorg"

        def transform_application(self, doc):
            spec = doc.get("spec", {})
            if "myOrgRepo" not in spec:
                return doc
            doc = {**doc, "spec": {**spec}}
            doc["spec"]["source"] = {"repoURL": doc["spec"].pop("myOrgRepo")}
            return doc

        def resolve_source(self, source, context):
            if source.repo_url.startswith("myorg://"):
                return FIXTURES / "simple-chart"
            return None

        def build_helm_command(self, cmd, source, context):
            return [*cmd, "--set", "replicaCount=3"]

        def after_walk(self, node):
            node.extra["reviewed"] = True

    registry = PluginRegistry([MyOrgPlugin()])
    monkeypatch.setattr("argocheck.parser.get_registry", lambda: registry)
    monkeypatch.setattr("argocheck.resolver.get_registry", lambda: registry)
    monkeypatch.setattr("argocheck.helm.get_registry", lambda: registry)
    monkeypatch.setattr("argocheck.walker.get_registry", lambda: registry)

    from argocheck.walker import walk

    doc = {
        "kind": "Application",
        "metadata": {"name": "myorg-app"},
        "spec": {"myOrgRepo": "myorg://fixture-chart"},
    }
    node = parse_application(doc)
    assert node.sources[0].repo_url == "myorg://fixture-chart"  # transform_application ran

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None  # resolve_source + build_helm_command both worked
    assert result.manifests[0]["spec"]["replicas"] == 3  # build_helm_command's --set took effect
    assert result.extra == {"reviewed": True}  # after_walk ran last


def test_helm_parameter_from_env_map_and_plugin_build_helm_command_both_apply(monkeypatch):
    """A plugin's build_helm_command runs *after* argocheck's own --set
    flags (including ones from a HelmSource's own parameters), so a plugin
    can rely on last-wins precedence to override rather than merge with
    them if it needs to."""
    class OverridePlugin(ArgocheckPlugin):
        def build_helm_command(self, cmd, source, context):
            return [*cmd, "--set", "replicaCount=9"]

    monkeypatch.setattr("argocheck.helm.get_registry", lambda: PluginRegistry([OverridePlugin()]))

    from argocheck.walker import walk

    doc = load_yaml_file(FIXTURES / "root-app-simple.yaml")
    node = parse_application(doc)
    node.sources[0].repo_url = str(FIXTURES / "simple-chart")
    node.sources[0].parameters = [HelmParameter("replicaCount", "2")]

    with tempfile.TemporaryDirectory() as tmp:
        result = walk(node, tmp_dir=Path(tmp))

    assert result.error is None
    assert result.manifests[0]["spec"]["replicas"] == 9
