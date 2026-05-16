# Example: basic app-of-apps

A minimal ArgoCD app-of-apps pattern with two child applications managed by a
single umbrella Helm chart. The entire `basic-app-of-apps` directory acts as
the git repository — individual charts live as subdirectories within it.

```
basic-app-of-apps/         ← the "repo"
  root-app.yaml            ← the root Application you point localargo at
  apps-chart/              ← umbrella chart (path: apps-chart)
  guestbook/               ← child chart (path: guestbook)
  podinfo/                 ← child chart (path: podinfo)
```

In each Application manifest, `repoURL` identifies the repository and `path`
identifies the Helm chart directory within it — exactly as ArgoCD expects.

## Setup

### 1. Copy this directory somewhere and enter it

```bash
cp -r examples/basic-app-of-apps ~/my-argo-experiment
cd ~/my-argo-experiment
```

### 2. Substitute the path placeholder

`root-app.yaml` and `apps-chart/values.yaml` both contain
`LOCALARGO_EXAMPLES_PATH` in place of the absolute path to this directory.
Replace it in one step:

```bash
grep -rl LOCALARGO_EXAMPLES_PATH . | xargs sed -i "s|LOCALARGO_EXAMPLES_PATH|$(pwd)|g"
```

### 3. (Optional) Initialise a git repo to simulate a real GitOps setup

ArgoCD fetches charts from git. To replicate this locally, initialise the
directory as a git repository so localargo can clone it via the `file://` URL:

```bash
git init && git add . && git commit -m "init"
```

Without this step the charts are read from disk as plain directories, which
is faster and perfectly fine for experimenting with values and templates.

### 4. Run localargo

```bash
# CLI — tree view
localargo root-app.yaml

# CLI — expand a specific child app
localargo root-app.yaml --expand guestbook

# Web interface
localargo-web
# then open http://localhost:8501, browse to root-app.yaml, and click Render
```

## How it works

`root-app.yaml` points at this directory as the repository (`repoURL:
file:///...`) and names `apps-chart` as the chart path within it. `apps-chart`
is a Helm chart whose templates render `kind: Application` manifests — one per
managed service. Each child Application also points back at the same repository
URL (from `repoBase` in `apps-chart/values.yaml`) with its own `path` field
selecting the correct chart subdirectory.

localargo discovers the child Applications in the rendered output and
recursively renders their charts, producing the full resource tree.

## Customising

- **Disable an app:** set `guestbook.enabled: false` in `apps-chart/values.yaml`.
- **Change image or replica count:** edit the per-app blocks in `apps-chart/values.yaml`.
- **Add a new app:** add a chart directory alongside `guestbook/`, add an
  Application template to `apps-chart/templates/`, and add a values block in
  `apps-chart/values.yaml`.
