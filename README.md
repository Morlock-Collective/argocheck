# argocheck

Local validator, dry-runner, and diff tool for ArgoCD app-of-apps Helm
structures, including multi-source Applications.

Point it at a root `kind: Application` manifest and it recursively renders the
entire application tree using your local `helm` binary — no cluster, no ArgoCD
installation required. The web interface also lets you structurally diff any
two apps in the rendered tree, resource by resource, for manual verification
of changes before they go anywhere near a cluster.

## What it does

argocheck walks an ArgoCD Application hierarchy the same way ArgoCD would:

1. Reads the root Application manifest
2. Renders the source — runs `helm template` if the source is a Helm chart (has `Chart.yaml`), or reads all YAML files directly if it is a plain manifest directory
3. Finds any `kind: Application` resources in the rendered output
4. Recurses into each child Application
5. Displays the full resource tree with per-app resource summaries

If anything fails — a missing chart, bad values, a broken template — the error
is shown inline at the point of failure, along with the exact `helm` command
that was run.

The web interface additionally supports diffing any two apps in the rendered
tree against each other, matching child apps and resources up structurally
and showing a line-level diff per changed resource — see [Diff mode](#diff-mode).

An optional [environment map](#environment-maps-value-trees) can also fan a
root app out across many environments (e.g. every cluster/namespace
combination) from a separate value-tree file, instead of hand-writing an
Application manifest per environment.

## What it does not do

- No ApplicationSet support — [value trees](#environment-maps-value-trees)
  cover the common local dry-run/diff use case, but this isn't a general
  ApplicationSet generator implementation
- No target cluster or server resolution — `spec.destination.namespace` is used as `--namespace` in `helm template`, but the server field is ignored
- No Sync waves or Sync hooks (those resources are treated like any other)

## Requirements

- Python 3.11+
- [`helm`](https://helm.sh/docs/intro/install/) on your `PATH`
- `git` on your `PATH` (only if you reference remote Git repositories)

## Installation

```bash
git clone <this repo>
cd argocheck
python -m venv .venv
source .venv/bin/activate
pip install -e .          # installs CLI and web interface
```

## Usage

### Web interface

```bash
argocheck-web
```

Starts a local web server on `http://localhost:8765` and opens it in your
browser. The interface is a Vue 3 single-page app served directly by the
backend — no separate deployment or build step required.

The interface provides:
- **Sidebar** — path input, recent-files list, filesystem browser, options
  (argocd-env toggle, max-depth), and the application tree with `├─`/`└─`
  hierarchy lines and ✅/❌ status icons
- **Detail panel** — source info, child app links, resources grouped by kind
  with collapsible syntax-highlighted YAML per resource
- **Application YAML toggle** — switches between the compact source view and
  the raw Application manifest YAML
- **Error display** — the failing helm command and wrapped stderr output
- **Diff mode** — structural comparison between any two apps in the rendered
  tree (see below)

#### Diff mode

Once a tree has rendered, any two applications in it can be compared
side-by-side — useful for checking that a "staging" and "prod" subtree (or any
two overlay/environment branches present in the same app-of-apps tree) only
differ where expected.

To use it:

1. Open the **Diff** section in the sidebar and check **Compare two branches**.
2. Assign **Branch A** and **Branch B**, either from the dropdowns or by
   right-clicking any row in the application tree and choosing *Assign to diff
   branch A/B* — the assigned rows get an `A`/`B` badge.

Child apps under each branch are matched by their path relative to the chosen
root, and resources within each matched pair are matched by `kind`/`name`.
Each resource is shown as **Identical**, **Changed** (with a line-level diff),
**Added**, or **Removed**, and each app subtree as a whole is flagged
**Only in A** / **Only in B** if it has no counterpart on the other side.
**Show identical** toggles whether unchanged apps/resources are hidden or
listed, and **Diff style** switches between a minimal (context-collapsed) and
full-context line diff.

Diff mode compares two subtrees of the *same* render — it does not fetch or
render a second revision, so to diff two git refs of the same chart, point
`repoURL`/`targetRevision` in your Application manifest at each ref as
separate sibling apps (or child apps) in the tree first, then diff those.

### CLI

```
argocheck [OPTIONS] ROOT_APP
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
argocheck root-app.yaml
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
argocheck root-app.yaml --expand frontend
```

**Dump the full YAML of a specific app:**

```bash
argocheck root-app.yaml --show backend
```

**With ArgoCD build environment variables:**

```bash
argocheck root-app.yaml --argocd-env
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

`argocheck` exits 0 if the entire tree renders without errors, and 1 if any
application fails.

## Environment maps (value trees)

An environment map (a.k.a. value tree) is an **optional add-on** to a normal
root Application/chart: instead of requiring a hand-written `kind:
Application` manifest per environment, it fans your existing root out across
a nested value map (e.g. cluster → namespace). Each leaf of the map becomes
its own rendered app, cloned from the exact same root you already pointed
`argocheck` at, with the tree path and the leaf's own key/value pairs passed
in as extra Helm `--set` parameters — so `clusters.prod.ns-a.replicaCount:
"3"` becomes `--set cluster=prod --set namespace=ns-a --set replicaCount=3`
on top of whatever that root app already declares. It's a separate file from
the root Application — it never contains a chart reference itself.

```yaml
argocheck_root: clusters                    # which top-level key holds the tree
argocheck_leaf_depth: 2                     # how many levels of keyed nesting before the leaf's own values
argocheck_variable_mappings:                # one entry per level (leaf_depth + 1), "" = no variable bound
  - ""                                      # depth 0: the "clusters" container itself
  - "cluster"                               # depth 1: each cluster name -> --set cluster=<key>
  - "namespace"                             # depth 2: each namespace name -> --set namespace=<key>

clusters:
  qa:
    ns-a:
      sourceRepo: https://github.com/my-org/qa-values.git
  prod:
    ns-a:
      sourceRepo: https://github.com/my-org/prod-values.git
    ns-b:
      sourceRepo: https://github.com/my-org/prod-values.git
```

Point `argocheck` at your root app exactly as usual, and pass the environment
map as an extra `--env-map` flag:

```bash
argocheck root-app.yaml --env-map env-map.yaml                     # render every leaf
argocheck root-app.yaml --env-map env-map.yaml --select prod       # only leaves under "prod"
argocheck root-app.yaml --env-map env-map.yaml --select prod/ns-a  # a single leaf
argocheck root-app.yaml --env-map env-map.yaml --show "my-app (prod/ns-a)"  # full YAML for one leaf
```

`--select` is only valid together with `--env-map`, and always matches by
environment-map path (`prod`, `prod/ns-a`), never by display name.

Each leaf is a full standalone instance of the root app, not a child of it —
so leaves are displayed and tracked as **separate top-level trees**, never
nested under a shared parent (there isn't one; the root app is only ever a
template here, and is itself never rendered/shown separately from its
leaves). Since every leaf is a clone of the same root, its own name (e.g.
`my-app`) would otherwise be identical, and thus invisible/useless, across
every leaf — so each leaf's display name is `<root app name> (<environment-map
path>)`, e.g. `my-app (prod/ns-a)`. `--show` and diff-mode branch assignment
identify a leaf by this full display name; `--select` identifies it by the
bare path instead.

The Helm *release name* passed to `helm template`, however, is **not**
changed per leaf — it stays whatever the un-fanned root app would use. The
display name above is an argocheck-only bookkeeping detail; it must never
leak into what Helm actually renders, or leaves would render different
resource names purely as a side effect of being fanned out, even when
nothing you specified was meant to cause a difference. Tree-supplied
parameters — both the path variables and the leaf's own key/value pairs —
are appended *after* the root app's own first source's `helm.parameters`, so
they win via Helm's last-`--set`-wins precedence. Only the root's first
source is touched (a multi-source root's other sources, e.g. a `$ref` values
source, are carried over unchanged), and this only applies to the root being
fanned out — any child Applications it renders resolve their own parameters
independently as usual.

Since every leaf renders as an ordinary app in the tree, [Diff mode](#diff-mode)
works across them for free — assign `my-app (prod/ns-a)` as Branch A and
`my-app (qa/ns-a)` as Branch B to compare environments resource-by-resource.

In the web interface, the root path input and **Render** button work exactly
as always — Render always renders, and its label indicates when it's about
to render a set of environments rather than a single root application. An
**Environment map (optional)** section in the sidebar lets you attach a
value-tree file, either as a file path or pasted YAML, with a dedicated
**Create map** button directly beneath those inputs. Create map is never
triggered by Render — clicking it enumerates the leaves (no `helm template`
calls yet — instant) and shows a checkbox tree right there in the same
section (a parent checkbox selects/deselects every leaf under it); if the
map couldn't be created (missing input, invalid file/YAML) it shows an
inline warning or error instead of doing nothing. With leaves checked,
Render's label becomes **Render selected (N)**; clicking it renders only
those, as separate top-level trees. Editing the environment map's path/YAML
afterwards requires clicking Create map again, so a stale selection is never
silently rendered.

## Chart source types

argocheck resolves chart sources the same way ArgoCD does:

### Local path

`repoURL` is a relative or absolute filesystem path. Relative paths are
resolved from the directory containing the Application manifest (for the root
app) or from the parent chart's directory (for child apps).

```yaml
source:
  repoURL: ./charts/my-app   # relative to this manifest file
  targetRevision: HEAD
```

If `repoURL` points at a directory that is itself a git repo and
`targetRevision` is set to anything other than `HEAD`, that revision (branch,
tag, or commit) is checked out into a scratch clone instead of using the
working tree as-is — mirroring ArgoCD's behavior for git sources. `HEAD` (the
default) always uses the working tree directly, uncommitted changes included.
The same local repo can be referenced at multiple revisions across sources
without conflict. Non-git local directories ignore `targetRevision` entirely.

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
> `--depth 1` into a temporary directory that is deleted when argocheck exits.

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
pytest -v
```
