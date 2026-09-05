# argocheck

A local validator, dry-runner, and diff tool for ArgoCD app-of-apps Helm
structures. It supports multi-source Applications.

Point argocheck at a root `kind: Application` manifest (or Helm Chart).
It recursively renders the full application tree with your local `helm` binary.
You need no cluster and no ArgoCD installation.

The web interface can also diff any two apps in the rendered tree, resource
by resource, so you can check changes before they reach a cluster.

## What it does

argocheck walks an ArgoCD Application hierarchy the same way ArgoCD does:

1. Reads the root Application manifest.
2. Renders the source. If the source is a Helm chart (has `Chart.yaml`), it runs `helm template`. Otherwise it reads the YAML files in the source directory directly.
3. Finds any `kind: Application` resources in the rendered output.
4. Recurses into each child Application.
5. Displays the full resource tree with per-app resource summaries.

If a step fails — a missing chart, bad values, a broken template — argocheck
shows the error at the point of failure, with the exact `helm` command it ran.

[Diff mode](#diff-mode) in the web interface compares any two apps in the
rendered tree. It matches child apps and resources structurally and shows a
line-level diff per changed resource.

An optional [environment map](#environment-maps-value-trees) can fan a root
app out across many environments (e.g. every cluster/namespace combination)
from one value-tree file, instead of a separate Application manifest per
environment.

## What it does not do

- No ApplicationSet support. [Environment maps](#environment-maps-value-trees) cover common local dry-run/diff cases, but argocheck is not a general ApplicationSet generator.
- No target cluster or server resolution. argocheck uses `spec.destination.namespace` as `--namespace` in `helm template` and ignores the server field.
- No special handling of Sync waves or Sync hooks. argocheck treats those resources like any other.

## Requirements

- Python 3.11+
- [`helm`](https://helm.sh/docs/intro/install/) on your `PATH`
- `git` on your `PATH` (only if you reference Git repositories)

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

Once a tree has rendered, you can compare any two applications in it
side-by-side. This is useful for checking that a "staging" and "prod"
subtree (or any two overlay/environment branches in the same app-of-apps
tree) differ only where you expect.

To use it:

1. Open the **Diff** section in the sidebar and check **Compare two branches**.
2. Assign **Branch A** and **Branch B**. Use the dropdowns, or right-click
   any row in the application tree and choose *Assign to diff branch A/B*.
   Assigned rows get an `A`/`B` badge.

argocheck matches child apps by their path relative to the chosen root, and
matches resources within each pair by `kind`/`name`. Each resource gets a
status: **Identical**, **Changed** (with a line-level diff), **Added**, or
**Removed**. An app subtree with no counterpart on the other side gets
**Only in A** or **Only in B**. **Show identical** shows or hides unchanged
apps/resources. **Diff style** switches between a minimal (context-collapsed)
and full-context line diff.

Diff mode compares two subtrees of the same render. It does not fetch or
render a second revision. To diff two git refs of the same chart, first add
each ref as a separate sibling app (or child app), each with its own
`repoURL`/`targetRevision`, then diff those two apps.

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

An environment map (also called a value tree) is an **optional add-on** to a
normal root Application/chart. It fans your existing root out across a
nested value map (e.g. cluster → namespace), instead of you writing one
`kind: Application` manifest per environment.

Each leaf of the map becomes its own rendered app, cloned from the same root
you pointed `argocheck` at. argocheck passes the leaf's tree path and its own
key/value pairs as extra Helm `--set` parameters, on top of what the root app
already declares. For example, `clusters.prod.ns-a.replicaCount: "3"` becomes
`--set cluster=prod --set namespace=ns-a --set replicaCount=3`.

An environment map is a separate file from the root Application. It never
contains a chart reference itself.

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

Point `argocheck` at your root app as usual, and add the environment map as
an extra `--env-map` flag:

```bash
argocheck root-app.yaml --env-map env-map.yaml                     # render every leaf
argocheck root-app.yaml --env-map env-map.yaml --select prod       # only leaves under "prod"
argocheck root-app.yaml --env-map env-map.yaml --select prod/ns-a  # a single leaf
argocheck root-app.yaml --env-map env-map.yaml --show "my-app (prod/ns-a)"  # full YAML for one leaf
```

`--select` only works together with `--env-map`. It always matches by
environment-map path (`prod`, `prod/ns-a`), never by display name.

argocheck turns each leaf of the map into a standalone instance of the root
app, and displays each as its own top-level tree. The root app itself is
only a template: argocheck never renders it separately from its leaves.

Every leaf is a clone of the same root, so the root's own name (e.g.
`my-app`) would be identical, and useless, on every leaf. So each leaf's
display name is `<root app name> (<environment-map path>)`, e.g.
`my-app (prod/ns-a)`. `--show` and diff-mode branch assignment identify a
leaf by this full display name. `--select` identifies a leaf by its bare
path instead.

The Helm *release name* that argocheck passes to `helm template` does not
change per leaf. It stays whatever the un-fanned root app would use. The
display name is an argocheck-only bookkeeping detail. It must never leak
into what Helm renders — otherwise leaves would render different resource
names as a side effect of the fan-out, even when nothing you specified was
meant to cause a difference.

argocheck appends tree-supplied parameters — the path variables and the
leaf's own key/value pairs — after the root app's own first source's
`helm.parameters`. They win through Helm's last-`--set`-wins precedence.
argocheck touches only the root's first source: a multi-source root's other
sources (e.g. a `$ref` values source) carry over unchanged. This fan-out
only affects the root app itself — any child Applications it renders resolve
their own parameters independently.

A leaf's own values aren't limited to scalars. Lists and nested mappings work
too:

```yaml
clusters:
  prod:
    ns-a:
      image:
        repository: custom-repo
        tag: v2
      tags: [blue, green]
```

argocheck passes scalars via plain `--set`, as above. `--set` cannot express
a list or a mapping, so argocheck JSON-encodes those and passes them via
Helm's `--set-json`.

One caveat, confirmed against real `helm template` runs: Helm gives its
flags a fixed precedence, regardless of the order you give them in —
`--set-literal` > `--set-string` > `--set` > `--set-json` > `-f` values
files. So a leaf's list or mapping value here always beats the chart's own
`values.yaml` and any `-f` file. But it loses to a plain `--set` parameter
that targets the *same key* elsewhere (e.g. in the root app's own
`helm.parameters`). This is an inherent Helm limitation — no flag choice or
order can change it. It only matters when a key set via a structured leaf
value collides with a scalar the root app's own parameters also set for that
exact key.

Every leaf renders as an ordinary app in the tree, so [Diff mode](#diff-mode)
works across them for free. Assign `my-app (prod/ns-a)` as Branch A and
`my-app (qa/ns-a)` as Branch B to compare two environments resource-by-resource.

In the web interface, the root path input and the **Render** button work as
always. Render's label changes to show when it is about to render a set of
environments instead of a single root application.

An **Environment map (optional)** section in the sidebar adds the rest:

- Attach a value-tree file as a file path or pasted YAML.
- Click **Create map**, directly below those inputs, to enumerate the leaves.
  This never calls `helm template`, so it's instant. A checkbox tree appears
  in the same section — a parent checkbox selects or deselects every leaf
  under it.
- If Create map fails (missing input, invalid file or YAML), it shows an
  inline warning or error instead of doing nothing.
- With leaves checked, Render's label becomes **Render selected (N)**.
  Clicking it renders only the checked leaves, as separate top-level trees.
- If you edit the environment map's path or YAML afterwards, you must click
  Create map again before you can render. This stops a stale selection from
  rendering silently.

Create map and Render are separate actions: Render never triggers Create map.

## Chart source types

argocheck resolves chart sources the same way ArgoCD does.

### Local path

`repoURL` is a relative or absolute filesystem path. For the root app,
argocheck resolves a relative path from the directory that holds the
Application manifest. For a child app, it resolves the path from the parent
chart's directory.

```yaml
source:
  repoURL: ./charts/my-app   # relative to this manifest file
  targetRevision: HEAD
```

`targetRevision: HEAD` (the default) always uses the working tree directly,
including uncommitted changes.

If `repoURL` points at a directory that is itself a git repo, and
`targetRevision` is anything other than `HEAD`, argocheck checks out that
revision (branch, tag, or commit) into a scratch clone instead — this
mirrors ArgoCD's behavior for git sources. The same local repo can appear at
multiple revisions across sources without conflict. A non-git local
directory ignores `targetRevision` entirely.

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

> Git sources need `git` on your `PATH`. argocheck clones the repo with
> `--depth 1` into a temporary directory, and deletes it when argocheck exits.

## Helm values precedence

argocheck applies values in this order (later entries override earlier
ones), matching ArgoCD's behavior:

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

argocheck fully supports multiple sources. Each source is either a **chart
source** (rendered by `helm template`) or a **ref source** (provides values
files only).

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

argocheck combines the non-Application resources from all chart sources onto
the same tree node. It renders each chart source with its own `releaseName`
(or the application name, if `releaseName` isn't set).

argocheck passes `spec.destination.namespace` as `--namespace` to `helm
template`, and ignores the destination server and cluster fields.

## Running the tests

```bash
pip install -e ".[dev]"
pytest -v
```
