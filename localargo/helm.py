"""Helm binary wrapper: command construction and subprocess execution."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .models import HelmSource


class HelmError(Exception):
    def __init__(self, message: str, cmd: list[str], stderr: str = ""):
        self.cmd = cmd
        self.stderr = stderr
        super().__init__(message)


def check_helm() -> str:
    """Return helm version string or raise HelmError if helm is not found."""
    try:
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        raise HelmError(
            "helm binary not found. Install helm: https://helm.sh/docs/intro/install/",
            cmd=["helm"],
        )
    except subprocess.CalledProcessError as e:
        raise HelmError(f"helm version check failed: {e.stderr}", cmd=["helm", "version"])


def build_template_cmd(
    chart_path: Path,
    source: HelmSource,
    release_name: str,
    namespace: str,
    tmp_dir: Path,
    argocd_env: bool = False,
) -> list[str]:
    """Build the `helm template` command list (does not execute it)."""
    cmd = ["helm", "template", release_name, str(chart_path)]
    cmd += ["--namespace", namespace]
    cmd.append("--include-crds")

    # Value files — relative to chart root
    for vf in source.value_files:
        resolved = (chart_path / vf).resolve()
        cmd += ["-f", str(resolved)]

    # Inline values string → temp file
    if source.values:
        tmp_vals = tmp_dir / f"{release_name}-values.yaml"
        tmp_vals.write_text(source.values)
        cmd += ["-f", str(tmp_vals)]

    # valuesObject → temp file (applied after values, higher precedence)
    if source.values_object:
        tmp_obj = tmp_dir / f"{release_name}-values-object.yaml"
        tmp_obj.write_text(yaml.dump(source.values_object))
        cmd += ["-f", str(tmp_obj)]

    # Parameters → --set / --set-string
    for param in source.parameters:
        flag = "--set-string" if param.force_string else "--set"
        cmd += [flag, f"{param.name}={param.value}"]

    # Optional ArgoCD build environment dummy values
    if argocd_env:
        cmd += [
            "--set", f"ARGOCD_APP_NAME={release_name}",
            "--set", f"ARGOCD_APP_NAMESPACE={namespace}",
            "--set", "ARGOCD_APP_REVISION=HEAD",
            "--set", "ARGOCD_APP_SOURCE_REPO_URL=",
            "--set", "ARGOCD_APP_SOURCE_PATH=.",
            "--set", "ARGOCD_APP_SOURCE_TARGET_REVISION=HEAD",
        ]

    return cmd


def run_template(
    chart_path: Path,
    source: HelmSource,
    release_name: str,
    namespace: str,
    tmp_dir: Path,
    argocd_env: bool = False,
) -> list[dict[str, Any]]:
    """Run `helm template` and return parsed YAML documents."""
    _maybe_update_dependencies(chart_path)

    cmd = build_template_cmd(chart_path, source, release_name, namespace, tmp_dir, argocd_env)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HelmError(
            f"helm template failed for release {release_name!r}",
            cmd=cmd,
            stderr=e.stderr,
        )

    docs: list[dict[str, Any]] = []
    for doc in yaml.safe_load_all(result.stdout):
        if isinstance(doc, dict):
            docs.append(doc)
    return docs


def helm_repo_add(repo_name: str, repo_url: str) -> None:
    cmd = ["helm", "repo", "add", repo_name, repo_url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HelmError(f"helm repo add failed: {e.stderr}", cmd=cmd, stderr=e.stderr)


def helm_repo_update() -> None:
    cmd = ["helm", "repo", "update"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HelmError(f"helm repo update failed: {e.stderr}", cmd=cmd, stderr=e.stderr)


def helm_pull(chart_ref: str, version: str | None, dest: Path, untar: bool = True) -> None:
    cmd = ["helm", "pull", chart_ref, "--destination", str(dest)]
    if version and version not in ("HEAD", ""):
        cmd += ["--version", version]
    if untar:
        cmd.append("--untar")
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HelmError(f"helm pull failed: {e.stderr}", cmd=cmd, stderr=e.stderr)


def _maybe_update_dependencies(chart_path: Path) -> None:
    """Run `helm dependency update` if the chart has deps but no charts/ dir."""
    chart_yaml = chart_path / "Chart.yaml"
    if not chart_yaml.exists():
        return

    with open(chart_yaml) as f:
        chart_meta = yaml.safe_load(f) or {}

    if not chart_meta.get("dependencies"):
        return

    charts_dir = chart_path / "charts"
    if charts_dir.exists() and any(charts_dir.iterdir()):
        return

    cmd = ["helm", "dependency", "update", str(chart_path)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HelmError(
            f"helm dependency update failed for {chart_path}",
            cmd=cmd,
            stderr=e.stderr,
        )
