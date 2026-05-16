# localargo

Local validator and dry-runner for ArgoCD app-of-apps Helm structures.

Point it at a root `kind: Application` manifest and it recursively renders the
entire application tree using your local `helm` binary — no cluster, no ArgoCD
installation required.

## What it does

localargo walks an ArgoCD Application hierarchy the same way ArgoCD would:

1. Reads the root Application manifest
2. Runs `helm template` with the configured values, parameters, and value files
3. Finds any `kind: Application` resources in the rendered output
4. Recurses into each child Application
5. Displays the full resource tree with per-app resource summaries

If anything fails — a missing chart, bad values, a broken template — the error
is shown inline at the point of failure, along with the exact `helm` command
that was run.

## What it does not do

- No ApplicationSet support
- No target cluster or server resolution — `spec.destination.namespace` is used as `--namespace` in `helm template`, but the server field is ignored
- No Sync waves or Sync hooks (those resources are treated like any other)

## Requirements

- Python 3.11+
- [`helm`](https://helm.sh/docs/intro/install/) on your `PATH`
- `git` on your `PATH` (only if you reference remote Git repositories)

## Installation

```bash
git clone <this repo>
cd localargo
python -m venv .venv
source .venv/bin/activate
pip install -e .          # CLI only
pip install -e ".[web]"   # CLI + web interface
```

## Usage

### Web interface

```bash
localargo-web
```

Opens a browser-based UI at `http://localhost:8501`. Enter the path to your
root Application manifest in the sidebar and click **Render**.

The interface shows:
- **Summary metrics** — total applications, resources, and error count
- **Application tree** — clickable sidebar list of all apps, indented by depth,
  with a ✓/✗ status indicator
- **Detail panel** — for the selected app: source info, child app links, and
  all rendered resources grouped by kind in tabs with syntax-highlighted YAML
- **Error detail** — for failed apps: the error message and the exact
  `helm template` command that was run

Requires `pip install -e ".[web]"`.

### CLI

```
localargo [OPTIONS] ROOT_APP
```

`ROOT_APP` is the path to a YAML file containing the root `kind: Application`
manifest.

### Options

| Option | Description |
|---|---|
| `--expand APP_NAME` | Inline-expand all rendered manifests for the named app in the tree. Repeatable. |
| `--show APP_NAME` | Print the full YAML of every manifest rendered by the named app instead of the tree. |
| `--argocd-env` | Inject dummy `ARGOCD_APP_*` values into every `helm template` call. |
| `--max-depth N` | Maximum recursion depth (default: 10). |
| `--version` | Print version and exit. |

### Examples

**Render and display the tree:**

```bash
localargo root-app.yaml
```

```
Using v3.17.0
✓ root-app [argocd]  —  0 resources
├── ✓ infra [argocd]  —  3 Deployments, 2 Services, 1 ConfigMap
└── ✓ apps [argocd]  —  0 resources
    ├── ✓ frontend [argocd]  —  1 Deployment, 1 Service, 1 Ingress
    └── ✓ backend [argocd]  —  1 Deployment, 1 Service
```

**Expand a specific app's manifests inline:**

```bash
localargo root-app.yaml --expand frontend
```

**Dump the full YAML of a specific app:**

```bash
localargo root-app.yaml --show backend
```

**With ArgoCD build environment variables:**

```bash
localargo root-app.yaml --argocd-env
```

This passes the following dummy values to each `helm template` call via
`--set`, so charts that reference ArgoCD build environment variables as Helm
values do not fail:

```
ARGOCD_APP_NAME                         = <releaseName>
ARGOCD_APP_NAMESPACE                    = <namespace>
ARGOCD_APP_REVISION                     = HEAD
ARGOCD_APP_SOURCE_REPO_URL              = (empty)
ARGOCD_APP_SOURCE_PATH                  = .
ARGOCD_APP_SOURCE_TARGET_REVISION       = HEAD
```

### Exit code

`localargo` exits 0 if the entire tree renders without errors, and 1 if any
application fails.

## Chart source types

localargo resolves chart sources the same way ArgoCD does:

### Local path

`repoURL` is a relative or absolute filesystem path. Relative paths are
resolved from the directory containing the Application manifest (for the root
app) or from the parent chart's directory (for child apps).

```yaml
source:
  repoURL: ./charts/my-app   # relative to this manifest file
  targetRevision: HEAD
```

### Helm chart repository (HTTP or OCI)

`chart` must be set. `targetRevision` is the chart version.

```yaml
source:
  repoURL: https://charts.bitnami.com/bitnami
  chart: nginx
  targetRevision: "18.1.5"
```

```yaml
source:
  repoURL: oci://registry-1.docker.io/bitnamicharts
  chart: nginx
  targetRevision: "18.1.5"
```

### Git repository

`path` specifies the chart directory within the repo. `targetRevision` is the
branch or tag to check out.

```yaml
source:
  repoURL: https://github.com/my-org/my-charts.git
  path: charts/my-app
  targetRevision: main
```

> Git sources require `git` on your `PATH`. The repo is cloned with
> `--depth 1` into a temporary directory that is deleted when localargo exits.

## Helm values precedence

Values are applied in this order (later entries override earlier ones),
matching ArgoCD's behavior:

1. `spec.source.helm.valueFiles` — value files relative to the chart root
2. `spec.source.helm.values` — inline YAML string
3. `spec.source.helm.valuesObject` — inline YAML object
4. `spec.source.helm.parameters` — individual key=value overrides (`--set`)

## Supported Application fields

### Single-source (`spec.source`)

```yaml
spec:
  source:
    repoURL: ...
    chart: ...              # Helm repo sources only
    path: ...               # Git repo sources only
    targetRevision: ...
    helm:
      releaseName: ...
      values: |             # inline values YAML string
        key: value
      valuesObject:         # inline values as a mapping
        key: value
      valueFiles:
        - values-prod.yaml
      parameters:
        - name: image.tag
          value: v1.2.3
        - name: replicas
          value: "3"
          forceString: true
      version: v2           # Helm API version hint
  destination:
    namespace: ...          # used as the --namespace flag in helm template
```

### Multi-source (`spec.sources`)

Multiple sources are fully supported. Each source is either a **chart source**
(rendered by `helm template`) or a **ref source** (provides values files only).

A source is a ref source when it has a `ref` field and no `chart` or `path`.
Chart sources may use `$<ref>/path` in `valueFiles` to reference files from a
ref source.

```yaml
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: my-app
      targetRevision: "2.1.0"
      helm:
        valueFiles:
          - $values/prod.yaml   # resolved from the "values" ref source below
    - repoURL: https://github.com/my-org/my-values.git
      targetRevision: main
      ref: values               # makes this source available as $values
  destination:
    namespace: default
```

All non-Application resources from all chart sources are combined onto the same
node in the tree. Each chart source is rendered with its own `releaseName` (or
the application name if not set).

`spec.destination.namespace` is passed as `--namespace` to `helm template`.
The destination server and cluster fields are ignored.

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```
