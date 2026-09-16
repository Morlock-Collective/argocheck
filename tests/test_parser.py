"""Tests for the Application manifest parser."""
import pytest
import yaml

from argocheck.parser import ParseError, load_yaml_file, parse_application, parse_all_applications


MINIMAL_APP = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: test-app
  namespace: argocd
spec:
  source:
    repoURL: ./my-chart
    targetRevision: HEAD
  destination:
    namespace: default
  project: default
"""

FULL_HELM_APP = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: full-app
  namespace: my-ns
spec:
  source:
    repoURL: https://charts.example.com
    chart: my-chart
    targetRevision: "1.2.3"
    helm:
      releaseName: my-release
      values: |
        foo: bar
      valuesObject:
        nested:
          key: value
      valueFiles:
        - values-prod.yaml
      parameters:
        - name: image.tag
          value: v1.0.0
        - name: replicas
          value: "3"
          forceString: true
      version: v2
  destination:
    namespace: prod
  project: default
"""

MULTISOURCE_APP = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: multi-app
  namespace: argocd
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: my-chart
      targetRevision: "1.2.3"
      helm:
        valueFiles:
          - $values/prod.yaml
    - repoURL: https://github.com/my-org/my-values.git
      targetRevision: main
      ref: values
  destination:
    namespace: default
  project: default
"""


def test_parse_minimal():
    doc = yaml.safe_load(MINIMAL_APP)
    node = parse_application(doc)
    assert node.name == "test-app"
    assert node.namespace == "argocd"
    assert not node.is_multi_source
    assert node.source.repo_url == "./my-chart"
    assert node.source.target_revision == "HEAD"
    assert node.source.chart is None
    assert node.source.value_files == []
    assert node.source.parameters == []


def test_parse_full_helm():
    doc = yaml.safe_load(FULL_HELM_APP)
    node = parse_application(doc)
    assert node.name == "full-app"
    assert node.namespace == "my-ns"
    s = node.source
    assert s.chart == "my-chart"
    assert s.target_revision == "1.2.3"
    assert s.release_name == "my-release"
    assert "foo: bar" in s.values
    assert s.values_object == {"nested": {"key": "value"}}
    assert s.value_files == ["values-prod.yaml"]
    assert len(s.parameters) == 2
    assert s.parameters[0].name == "image.tag"
    assert s.parameters[0].value == "v1.0.0"
    assert s.parameters[0].force_string is False
    assert s.parameters[1].force_string is True
    assert s.version == "v2"


def test_parse_wrong_kind():
    doc = {"kind": "Deployment", "metadata": {"name": "x"}}
    with pytest.raises(ParseError, match="Expected kind: Application"):
        parse_application(doc)


def test_parse_multisource():
    doc = yaml.safe_load(MULTISOURCE_APP)
    node = parse_application(doc)
    assert node.name == "multi-app"
    assert node.is_multi_source
    assert len(node.sources) == 2

    chart_src = node.sources[0]
    assert chart_src.chart == "my-chart"
    assert chart_src.value_files == ["$values/prod.yaml"]
    assert chart_src.ref is None

    ref_src = node.sources[1]
    assert ref_src.ref == "values"
    assert ref_src.repo_url == "https://github.com/my-org/my-values.git"
    assert ref_src.is_ref_only


def test_parse_all_applications_filters():
    multi = MINIMAL_APP + "\n---\n" + "kind: Deployment\nmetadata:\n  name: dep\n"
    apps = parse_all_applications(multi)
    assert len(apps) == 1
    assert apps[0].name == "test-app"


def test_parse_dollar_ref_value_file_allowed_in_multisource():
    # $ref syntax is valid in multi-source context — parser should not reject it
    doc = yaml.safe_load(MULTISOURCE_APP)
    node = parse_application(doc)
    assert node.sources[0].value_files == ["$values/prod.yaml"]


def test_load_yaml_file_reads_utf8_content(tmp_path):
    """A non-ASCII value (e.g. in metadata.name) must round-trip — the file
    is read as UTF-8 explicitly, not whatever the platform locale defaults
    to."""
    manifest = tmp_path / "app.yaml"
    manifest.write_text(
        "apiVersion: argoproj.io/v1alpha1\n"
        "kind: Application\n"
        "metadata:\n"
        "  name: café-app\n"
        "  namespace: argocd\n"
        "spec:\n"
        "  source:\n"
        "    repoURL: ./chart\n"
        "  project: default\n",
        encoding="utf-8",
    )
    doc = load_yaml_file(manifest)
    assert doc["metadata"]["name"] == "café-app"


def test_load_yaml_file_rejects_non_utf8_content_clearly(tmp_path):
    """A file that isn't valid UTF-8 (e.g. Latin-1 with a byte sequence
    that's invalid UTF-8) must raise a clear ParseError, not an unhandled
    UnicodeDecodeError."""
    manifest = tmp_path / "app.yaml"
    manifest.write_bytes("name: café\n".encode("latin-1"))  # 0xE9 is invalid UTF-8 alone

    with pytest.raises(ParseError, match="not valid UTF-8"):
        load_yaml_file(manifest)
