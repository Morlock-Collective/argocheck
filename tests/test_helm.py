"""Tests for helm command construction."""
import tempfile
from pathlib import Path

from localargo.helm import build_template_cmd
from localargo.models import HelmParameter, HelmSource


def _source(**kwargs) -> HelmSource:
    defaults = dict(repo_url="./chart")
    defaults.update(kwargs)
    return HelmSource(**defaults)


def test_basic_cmd():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chart = Path("/some/chart")
        src = _source()
        cmd = build_template_cmd(chart, src, "my-release", "default", tmp_dir)
        assert cmd[:4] == ["helm", "template", "my-release", str(chart)]
        assert "--namespace" in cmd
        assert "default" in cmd
        assert "--include-crds" in cmd


def test_value_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chart = Path(tmp) / "chart"
        chart.mkdir()
        vf = chart / "prod.yaml"
        vf.write_text("x: 1")
        src = _source(value_files=["prod.yaml"])
        cmd = build_template_cmd(chart, src, "r", "ns", tmp_dir)
        assert "-f" in cmd
        assert str(vf.resolve()) in cmd


def test_inline_values_written_to_file():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chart = Path("/chart")
        src = _source(values="foo: bar\n")
        cmd = build_template_cmd(chart, src, "r", "ns", tmp_dir)
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        values_file = Path(cmd[f_idx + 1])
        assert values_file.read_text() == "foo: bar\n"


def test_values_object_written_after_values():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chart = Path("/chart")
        src = _source(values="a: 1\n", values_object={"b": 2})
        cmd = build_template_cmd(chart, src, "r", "ns", tmp_dir)
        f_flags = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-f"]
        assert len(f_flags) == 2
        assert "values-object" in f_flags[1]


def test_parameters_set_flags():
    with tempfile.TemporaryDirectory() as tmp:
        src = _source(parameters=[
            HelmParameter("image.tag", "v1"),
            HelmParameter("replicas", "3", force_string=True),
        ])
        cmd = build_template_cmd(Path("/c"), src, "r", "ns", Path(tmp))
        assert "--set" in cmd
        assert "image.tag=v1" in cmd
        assert "--set-string" in cmd
        assert "replicas=3" in cmd


def test_argocd_env_flags():
    with tempfile.TemporaryDirectory() as tmp:
        src = _source()
        cmd = build_template_cmd(Path("/c"), src, "my-rel", "my-ns", Path(tmp), argocd_env=True)
        joined = " ".join(cmd)
        assert "ARGOCD_APP_NAME=my-rel" in joined
        assert "ARGOCD_APP_NAMESPACE=my-ns" in joined


def test_ref_value_file_resolved_via_ref_map():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chart = Path("/chart")
        values_dir = Path(tmp) / "values-repo"
        values_dir.mkdir()
        (values_dir / "prod.yaml").write_text("env: prod\n")

        src = _source(value_files=["$vals/prod.yaml"])
        ref_map = {"vals": values_dir}
        cmd = build_template_cmd(chart, src, "r", "ns", tmp_dir, ref_map=ref_map)

        f_flags = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-f"]
        assert len(f_flags) == 1
        assert f_flags[0] == str((values_dir / "prod.yaml").resolve())


def test_ref_value_file_unknown_ref_raises():
    from localargo.helm import HelmError
    with tempfile.TemporaryDirectory() as tmp:
        src = _source(value_files=["$missing/prod.yaml"])
        with pytest.raises(HelmError, match="unknown ref"):
            build_template_cmd(Path("/c"), src, "r", "ns", Path(tmp), ref_map={})


import pytest


def test_read_helm_repos_parses_helm_env(monkeypatch):
    """_read_helm_repos extracts HELM_CONFIG_HOME from `helm env` output."""
    import subprocess
    from localargo.helm import _read_helm_repos
    import yaml, tempfile, os

    with tempfile.TemporaryDirectory() as config_dir:
        # Write a minimal repositories.yaml in the fake config dir
        repos_yaml = {
            "apiVersion": "",
            "repositories": [{"name": "myrepo", "url": "https://charts.example.com"}],
        }
        (Path(config_dir) / "repositories.yaml").write_text(yaml.dump(repos_yaml))

        # Fake `helm env` output — quotes around the value, matching real helm output
        fake_env = f'HELM_CACHE_HOME="{config_dir}/cache"\nHELM_CONFIG_HOME="{config_dir}"\nHELM_DATA_HOME="{config_dir}/data"\n'

        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"stdout": fake_env, "returncode": 0})(),
        )

        repos = _read_helm_repos()
        assert repos == {"myrepo": "https://charts.example.com"}


def test_ensure_dep_repos_raises_for_missing_alias(tmp_path, monkeypatch):
    """Alias-based dependency with no matching registered repo raises HelmError."""
    from localargo.helm import HelmError, _ensure_dep_repos

    # Patch _read_helm_repos to return an empty registry
    monkeypatch.setattr("localargo.helm._read_helm_repos", lambda: {})

    deps = [{"name": "redis", "version": "17.0.0", "repository": "@myrepo"}]
    with pytest.raises(HelmError, match="unregistered helm repo alias"):
        _ensure_dep_repos(deps, tmp_path)


def test_ensure_dep_repos_ok_when_alias_registered(tmp_path, monkeypatch):
    """No error when the alias is already in the helm repo registry."""
    from localargo.helm import _ensure_dep_repos

    monkeypatch.setattr(
        "localargo.helm._read_helm_repos",
        lambda: {"myrepo": "https://charts.example.com"},
    )

    deps = [{"name": "redis", "version": "17.0.0", "repository": "@myrepo"}]
    _ensure_dep_repos(deps, tmp_path)  # must not raise


def test_ensure_dep_repos_auto_registers_url(tmp_path, monkeypatch):
    """URL-based dependency is auto-registered when not in the registry."""
    from localargo.helm import _ensure_dep_repos

    added = []
    monkeypatch.setattr("localargo.helm._read_helm_repos", lambda: {})
    monkeypatch.setattr("localargo.helm.helm_repo_add", lambda name, url: added.append((name, url)))
    monkeypatch.setattr("localargo.helm.helm_repo_update", lambda: None)

    url = "https://charts.bitnami.com/bitnami"
    deps = [{"name": "nginx", "version": "18.0.0", "repository": url}]
    _ensure_dep_repos(deps, tmp_path)

    assert len(added) == 1
    assert added[0][1] == url
