# Example: basic app-of-apps

A minimal ArgoCD app-of-apps pattern with two child applications managed by a
single umbrella Helm chart.

```
root-app.yaml          ← the root Application you point localargo at
apps-chart/            ← umbrella chart; renders one Application per service
guestbook/             ← child chart: a simple web application
podinfo/               ← child chart: a minimal API server
```

The umbrella chart (`apps-chart`) references the child charts by their path on
disk, expressed as `file://` URLs. This mirrors how you would reference charts
in a real git repository (`https://github.com/...`), but works entirely
locally so you can experiment without network access or a running cluster.

## Setup

### 1. Copy this directory somewhere and enter it

```bash
cp -r examples/basic-app-of-apps ~/my-argo-experiment
cd ~/my-argo-experiment
```

### 2. Substitute the path placeholder

`apps-chart/values.yaml` contains the string `LOCALARGO_EXAMPLES_PATH` in
place of the absolute path to this directory. Replace it:

```bash
sed -i "s|LOCALARGO_EXAMPLES_PATH|$(pwd)|g" apps-chart/values.yaml
```

### 3. (Optional) Initialise git repos to simulate a real GitOps setup

ArgoCD fetches charts from git. To replicate this locally, initialise each
chart directory as its own git repository and reference them with `file://`:

```bash
for chart in apps-chart guestbook podinfo; do
    git -C "$chart" init -q
    git -C "$chart" add .
    git -C "$chart" commit -qm "init"
done
```

The `file://` URLs in `apps-chart/values.yaml` will then cause localargo to
clone each repo exactly as ArgoCD would, rather than reading files directly.

> Without this step the charts are read from disk as plain directories, which
> is faster and perfectly fine for experimenting with values and templates.

### 4. Run localargo

```bash
# CLI — tree view
localargo root-app.yaml

# CLI — expand a specific app
localargo root-app.yaml --expand guestbook

# Web interface
localargo-web
# then open http://localhost:8501, browse to root-app.yaml, and click Render
```

## How it works

`root-app.yaml` points at `./apps-chart` (relative to itself, no placeholder
needed). `apps-chart` is a Helm chart whose templates render `kind: Application`
manifests — one per managed service. Each child Application's `repoURL` is
built from the `repoBase` value in `apps-chart/values.yaml`, which is the
`file://` URL you substituted in step 2.

localargo discovers the child Applications in the rendered output and
recursively renders their charts, producing the full resource tree.

## Customising

- **Disable an app:** set `guestbook.enabled: false` in `apps-chart/values.yaml`.
- **Change image or replica count:** edit the per-app blocks in `apps-chart/values.yaml`.
- **Add a new app:** add a new Application template to `apps-chart/templates/`
  and a corresponding chart directory, following the existing patterns.
