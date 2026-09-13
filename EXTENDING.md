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

## What's not covered (yet)

No hooks exist yet for the per-app detail panel or the resource viewer
(no custom tabs/columns there), and the core CLI's own flags aren't
programmatically extensible — a plugin's hooks apply automatically no matter
how `argocheck.cli:main` is invoked, but adding new CLI flags means writing
your own Click command around the same building blocks
(`argocheck.parser`, `argocheck.resolver`, `argocheck.walker`).
