"""Structured help content, shared by the CLI guide and the web UI's help modal.

Each topic is a list of typed blocks (paragraph/heading/list/code) instead of
markdown, so both sides can render it without a markdown-parsing dependency:
the CLI via display.py's rich-based renderer, the web UI via a small Vue
block renderer that reuses the syntax highlighter it already bundles.
"""
from __future__ import annotations

from typing import Any

# A block is one of:
#   {"type": "p",    "text": "..."}
#   {"type": "h",    "text": "..."}
#   {"type": "list", "items": ["...", "..."]}
#   {"type": "code", "lang": "yaml"|"bash", "text": "..."}

HELP_TOPICS: list[dict[str, Any]] = [
    {
        "id": "basics",
        "title": "Basics",
        "blocks": [
            {"type": "p", "text":
                "argocheck validates and dry-runs an ArgoCD app-of-apps Helm "
                "structure on your machine. It needs no cluster and no ArgoCD "
                "installation."},
            {"type": "p", "text":
                "Point it at a root kind: Application manifest, or a Helm "
                "chart directory. It walks the tree the same way ArgoCD would:"},
            {"type": "list", "items": [
                "Reads the root Application manifest.",
                "Renders the source: runs helm template for a Helm chart "
                "(has Chart.yaml), or reads the YAML files directly for a "
                "plain manifest directory.",
                "Finds any kind: Application resources in the rendered output.",
                "Recurses into each child Application.",
                "Displays the full resource tree with per-app resource summaries.",
            ]},
            {"type": "p", "text":
                "If a step fails — a missing chart, bad values, a broken "
                "template — argocheck shows the error at the point of "
                "failure, with the exact helm command it ran."},
            {"type": "p", "text":
                "The web interface adds two more tools on top of this: Diff "
                "mode (compare any two apps in the rendered tree) and "
                "Environment maps (fan a root app out across many "
                "environments from one file). See their own topics for "
                "details."},
        ],
    },
    {
        "id": "options",
        "title": "Options",
        "blocks": [
            {"type": "p", "text":
                "These settings apply to every render, on both the CLI and "
                "the web interface's Options section:"},
            {"type": "list", "items": [
                "Inject ARGOCD_APP_* dummy values — makes charts that "
                "reference ArgoCD's build environment variables (repoURL, "
                "targetRevision, and so on) render the way ArgoCD would "
                "render them. CLI: --argocd-env.",
                "Max recursion depth — how many levels of child Application "
                "to follow before stopping. Default: 10. CLI: --max-depth N.",
                "Ignore targetRevision — resolve every source as if "
                "targetRevision were unset: a local git repo's working tree "
                "as-is, a remote git repo's default branch, or a Helm repo "
                "chart's latest version. Useful when testing against a local "
                "checkout that would normally pin a specific revision. Does "
                "not affect rendering. CLI: --ignore-target-revision.",
                "Values override — extra Helm values (YAML), used only "
                "when the root is a bare chart directory rather than an "
                "Application manifest. Web interface only.",
            ]},
        ],
    },
    {
        "id": "environment-map",
        "title": "Environment maps",
        "blocks": [
            {"type": "p", "text":
                "An environment map (also called a value tree) is an "
                "optional add-on to a normal root Application or chart. It "
                "fans your existing root out across a nested value map (e.g. "
                "cluster → namespace), instead of you writing one "
                "Application manifest per environment."},
            {"type": "p", "text":
                "Each leaf of the map becomes its own rendered app, cloned "
                "from the same root. argocheck passes the leaf's tree path "
                "and its own key/value pairs as extra Helm --set parameters, "
                "on top of what the root app already declares:"},
            {"type": "code", "lang": "yaml", "text":
                'argocheck_root: clusters\n'
                'argocheck_leaf_depth: 2\n'
                'argocheck_variable_mappings:\n'
                '  - ""\n'
                '  - "cluster"\n'
                '  - "namespace"\n'
                '\n'
                'clusters:\n'
                '  qa:\n'
                '    ns-a:\n'
                '      sourceRepo: https://github.com/my-org/qa-values.git\n'
                '  prod:\n'
                '    ns-a:\n'
                '      sourceRepo: https://github.com/my-org/prod-values.git\n'
                '    ns-b:\n'
                '      sourceRepo: https://github.com/my-org/prod-values.git\n'
            },
            {"type": "p", "text":
                "argocheck_root names the top-level key that holds the tree "
                "(clusters here). argocheck_leaf_depth counts the levels of "
                "keyed nesting before each leaf's own values — 2 here, for "
                "cluster then namespace."},
            {"type": "p", "text":
                "argocheck_variable_mappings has one entry per level "
                "(leaf_depth + 1): the first entry is for the root container "
                "itself (\"\" here, meaning no variable), then one entry per "
                "nested level. Here, each cluster name becomes "
                "--set cluster=<name>, and each namespace name becomes "
                "--set namespace=<name>."},
            {"type": "p", "text":
                "For the example above, clusters.prod.ns-b renders with "
                "--set cluster=prod --set namespace=ns-b "
                "--set sourceRepo=https://github.com/my-org/prod-values.git, "
                "on top of the root app's own parameters."},
            {"type": "p", "text":
                "A leaf's own values aren't limited to scalars — lists and "
                "nested mappings work too, passed via Helm's --set-json."},
            {"type": "code", "lang": "bash", "text":
                "argocheck root-app.yaml --env-map env-map.yaml                     # every leaf\n"
                "argocheck root-app.yaml --env-map env-map.yaml --select prod       # only leaves under \"prod\"\n"
                "argocheck root-app.yaml --env-map env-map.yaml --select prod/ns-a  # a single leaf\n"
            },
            {"type": "p", "text":
                "Web interface: attach the environment map in its own "
                "sidebar section (file path or inline YAML). Click Create "
                "map to enumerate the leaves — instant, no helm calls yet — "
                "check which ones you want, then click Render."},
            {"type": "p", "text":
                "Once inline YAML has produced a map, Save to file… writes "
                "it to a file and switches the section to File path pointing "
                "at it, for reuse or version control."},
            {"type": "p", "text":
                "Each leaf is a full standalone instance of the root app, "
                "not a child of it, so leaves appear as separate top-level "
                "trees. Since every leaf clones the same root, its display "
                "name is <root app name> (<environment-map path>), e.g. "
                "my-app (prod/ns-a), so you can tell them apart."},
        ],
    },
    {
        "id": "diffing",
        "title": "Diff mode",
        "blocks": [
            {"type": "p", "text":
                "Diff mode structurally compares any two applications in the "
                "currently rendered tree — useful for checking that two "
                "environments, or two branches of the same app-of-apps tree, "
                "differ only where you expect."},
            {"type": "p", "text":
                "It compares two subtrees of the same render. It does not "
                "fetch or render a second revision — to diff two git refs of "
                "the same chart, first add each ref as a separate app in the "
                "tree (e.g. via an environment map), then diff those two apps."},
            {"type": "list", "items": [
                "Turn it on with the checkbox in the \"Diff\" section heading.",
                "Assign Branch A and Branch B from the dropdowns, or right-click any row in the tree and choose \"Assign to diff branch A/B\".",
                "argocheck matches child apps by their path relative to the chosen root, and matches resources within each pair by kind/name.",
                "It also diffs each matched app's own Application resource (repoURL, targetRevision, Helm values, ...), separate from the resources it renders — so a change to the Application definition itself always shows up.",
            ]},
            {"type": "p", "text":
                "Each resource (and each app's own Application entry) gets a "
                "status: Identical, Differs (with a line-level diff), Added, "
                "or Removed. An app subtree with no counterpart on the other "
                "side gets Only in A or Only in B."},
            {"type": "p", "text":
                "Show identical toggles whether unchanged apps/resources are "
                "hidden. Diff style switches between a minimal "
                "(context-collapsed) and full-context line diff."},
        ],
    },
]

_BY_ID = {t["id"]: t for t in HELP_TOPICS}


def get_topic(topic_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(topic_id)
