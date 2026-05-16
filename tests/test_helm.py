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
        # A -f flag should be present pointing to a tmp file
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
        # valuesObject file comes after values file
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
