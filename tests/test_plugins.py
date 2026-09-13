"""Tests for the plugin discovery/hook system (argocheck.plugins)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from argocheck.plugins import (
    ArgocheckPlugin,
    HelmContext,
    PluginRegistry,
    ResolveContext,
    load_plugins,
)


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


def test_load_plugins_skips_a_broken_entry_point(capsys):
    class FakeEntryPoint:
        name = "broken"

        def load(self):
            raise ImportError("nope")

    with patch("argocheck.plugins.entry_points", return_value=[FakeEntryPoint()]):
        registry = load_plugins()

    assert registry.plugins == []
    assert "broken" in capsys.readouterr().err
