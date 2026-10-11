# Extending argocheck

This is for people building on top of argocheck, not people running it —
see [README.md](README.md) for that. If your organization has its own
chart/app-structure conventions (a custom `repoURL` scheme, custom
Application manifest fields, extra Helm parameters, org-specific sidebar UI),
you can tailor argocheck to them from your own Python package — say,
`spotify-argocheck` — without forking argocheck itself.

With no plugins installed, argocheck behaves exactly as documented in the
README. Everything below is additive.

## Writing a plugin

Subclass `ArgocheckPlugin` (`argocheck.plugins`) and override only the hooks
you need — every hook defaults to a no-op/passthrough:

```python
from pathlib import Path
from argocheck.plugins import ArgocheckPlugin

class SpotifyPlugin(ArgocheckPlugin):
    name = "spotify"

    def resolve_source(self, source, context):
        """Recognize our own repoURL scheme; None falls through to
        argocheck's normal local/git/Helm-repo resolution."""
        if source.repo_url.startswith("spotify-charts://"):
            return resolve_our_own_way(source.repo_url)
        return None

    def transform_application(self, doc):
        """Normalize our own Application manifest conventions into the
        fields argocheck already understands. Runs for the root app and
        every child Application discovered while walking the tree."""
        return doc

    def build_helm_command(self, cmd, source, context):
        """Post-process the constructed `helm template` argv."""
        return [*cmd, "--set", "org=spotify"]

    def after_walk(self, node):
        """Inspect/annotate a fully-rendered AppNode. Attach data via
        node.extra (a plain dict, unused by argocheck itself) so a
        frontend plugin component can read it back from the API response."""
        node.extra["reviewed"] = node.name in KNOWN_APPS

    def register_routes(self, app):
        """Mount extra FastAPI routes/static files directly."""
        app.get("/api/spotify/status")(lambda: {"ok": True})

    def help_topics(self):
        """Extra guide topics, merged into both the CLI guide and the web
        interface's help modal. Same {id, title, blocks} shape as
        argocheck.help_content.HELP_TOPICS."""
        return [{"id": "spotify", "title": "Spotify conventions", "blocks": [
            {"type": "p", "text": "..."},
        ]}]

    def frontend_assets(self):
        """Extra JS files served under /plugin-static/<name>/ and injected
        as <script> tags into the web interface's index.html."""
        return [Path(__file__).parent / "static" / "main.js"]
```

A plugin's `__init__`/hooks raising is isolated: argocheck logs a warning to
stderr and skips that call, it never crashes the caller. A broken plugin
never prevents argocheck itself from starting.

## Registering it

Declare an `argocheck.plugins` entry point in your own package's
`pyproject.toml`:

```toml
[project]
name = "spotify-argocheck"
dependencies = ["argocheck"]

[project.entry-points."argocheck.plugins"]
spotify = "spotify_argocheck.plugin:SpotifyPlugin"

[project.scripts]
spotify-argocheck = "argocheck.cli:main"
```

That's the whole distribution story: `spotify-argocheck` reuses argocheck's
own CLI (`argocheck.cli:main`) and web server (`argocheck.server:app`, or
`argocheck-web`) unchanged — both auto-discover every installed plugin via
this entry point at startup. You don't need your own CLI or server at all
unless you want one; if you do, `argocheck.server.create_app()` is a factory
you can call directly (it also auto-discovers plugins by default).

An entry point may point at an `ArgocheckPlugin` subclass (instantiated with
no arguments), a zero-argument factory function returning an instance, or an
already-built instance.

## Extending the web interface

Since the web interface ships with no JS build step, a `frontend_assets()`
file registers itself through a small global queue, order-independent with
respect to when `app.js` itself runs:

```js
window.argocheckPlugin(function (registry) {
  registry.addSidebarSection({
    title: "Spotify",
    component: { template: "<div>...</div>" },  // any Vue component
  });

  registry.addRenderInterceptor({
    beforeRequest(req) { /* mutate the /api/render payload before it's sent */ },
    afterResult(result) { /* inspect/annotate the returned tree */ },
  });

  registry.addHelpTopics([
    { id: "spotify-js", title: "Spotify (JS)", blocks: [{ type: "p", text: "..." }] },
  ]);
});
```

`addSidebarSection`'s component renders inside a collapsible section matching
the built-in ones (Options, Diff, etc.) — you only supply the body. Data a
Python-side `after_walk` hook attached via `node.extra` comes back on each
node in the `/api/render` response, so a sidebar/render-interceptor component
can read it from there.

Your component also receives a read-only `context` prop, so it can react to
the same state the built-in UI uses instead of running a disconnected copy
of it:

```js
component: {
  props: ["context"],
  template: "<div>Current root: {{ context.rootPath }}</div>",
  watch: {
    "context.rootPath"(path) { /* react to what the user picked */ },
  },
}
```

`context.rootPath` and `context.isRendering` are both read-only computed
values mirroring the root path input and the in-flight-render state. To
actually influence what renders, mutate the payload from
`renderInterceptors.beforeRequest` as above — `context` itself can't be
written to, so plugins can't fight over shared core state.

## Example: a config-repo plugin

A common shape for an org-specific plugin: a standardized "app-of-apps"
chart lives in one shared repo, and developers only work in separate config
repos that don't themselves contain that chart. The plugin's job is to let
someone point argocheck at their config repo directly, recognize it,
fetch/cache the shared chart, and derive render parameters (e.g. cluster and
namespace) from the repo's own layout instead of a hand-written Application
manifest or environment map.

None of this needs new argocheck hooks — it combines the ones already
above:

**1. Recognize the repo and discover its options**, via your own endpoint
(`register_routes`) — ordinary filesystem code, nothing argocheck-specific:

```python
def register_routes(self, app):
    @app.get("/api/myorg/detect")
    def detect(path: str):
        repo = Path(path)
        if not (repo / ".myorg-repo.yaml").exists():
            return {"recognized": False}
        clusters = sorted(p.name for p in (repo / "clusters").iterdir() if p.is_dir())
        return {"recognized": True, "clusters": clusters}
```

**2. Fetch/cache the shared chart once**, via `resolve_source` — recognize
your own convention (a literal URL, a custom scheme, whatever you choose)
and return a path from your own persistent cache instead of argocheck's
default per-run temp directory:

```python
import fcntl, hashlib

_CACHE_ROOT = Path.home() / ".cache" / "myorg-argocheck"

def resolve_source(self, source, context):
    if source.repo_url != OUR_AOA_CHART_URL:
        return None
    dest = _CACHE_ROOT / hashlib.sha1(source.repo_url.encode()).hexdigest()[:12]
    lock_file = _CACHE_ROOT / f"{dest.name}.lock"
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)  # serialize concurrent first-time downloads
        if not (dest / "Chart.yaml").exists():
            download_chart(source.repo_url, dest)  # your own git clone/helm pull
    return dest
```

This caching and locking is entirely your plugin's own responsibility —
argocheck has no opinion on where or how you persist it, beyond the `Path`
you return. A simple `fcntl`-based lock (as above) is enough to stop two
concurrent web requests from racing into a half-downloaded chart. It's
POSIX-only, which is fine for a local dev tool.

**3. Derive parameters from the repo layout**, as a plain dict matching the
environment-map shape documented in the "Environment maps" guide topic
(`argocheck_root`/`argocheck_leaf_depth`/`argocheck_variable_mappings`) —
nothing downstream cares whether that dict came from a YAML file a human
wrote or one your endpoint assembled from `clusters/<cluster>/<namespace>/`
directories:

```python
import yaml

def build_env_map_yaml(repo: Path) -> str:
    clusters = {c.name: {n.name: {} for n in c.iterdir() if n.is_dir()}
                for c in (repo / "clusters").iterdir() if c.is_dir()}
    return yaml.dump({
        "argocheck_root": "clusters",
        "argocheck_leaf_depth": 2,
        "argocheck_variable_mappings": {1: "cluster", 2: "namespace"},
        "clusters": clusters,
    })
```

**4. Wire it into the existing Render button**, from your sidebar component:
watch `context.rootPath`, call your `/api/myorg/detect` endpoint, and if
recognized, show your own cluster/namespace pickers in place of the normal
flow. On render, a `renderInterceptor.beforeRequest` sets the already-cached
chart directory as `payload.path` and your derived map as
`payload.env_map_yaml`. Both are existing `RenderRequest` fields, so this
needs no new server-side plumbing:

```js
window.argocheckPlugin(function (registry) {
  let selection = null;  // written by the sidebar component below

  registry.addSidebarSection({
    title: "My Org",
    component: {
      props: ["context"],
      data() { return { recognized: false, clusters: [] }; },
      watch: {
        async "context.rootPath"(path) {
          const info = await fetch(`/api/myorg/detect?path=${encodeURIComponent(path)}`).then(r => r.json());
          this.recognized = info.recognized;
          this.clusters = info.clusters ?? [];
        },
      },
      methods: {
        pick(cluster, namespace) { selection = { cluster, namespace }; },
      },
      template: `<div v-if="recognized">...cluster/namespace pickers calling pick()...</div>`,
    },
  });

  registry.addRenderInterceptor({
    beforeRequest(req) {
      if (selection) {
        req.path = /* the cached chart directory your backend resolved */;
        req.env_map_yaml = /* the YAML your backend derived for `selection` */;
      }
    },
  });
});
```

The user still clicks the same Render button the rest of the app uses. The
plugin only ever influences the payload that button sends.

## What's not covered (yet)

No hooks exist yet for the per-app detail panel or the resource viewer
(no custom tabs/columns there), and the core CLI's own flags aren't
programmatically extensible — a plugin's hooks apply automatically no matter
how `argocheck.cli:main` is invoked, but adding new CLI flags means writing
your own Click command around the same building blocks
(`argocheck.parser`, `argocheck.resolver`, `argocheck.walker`).
