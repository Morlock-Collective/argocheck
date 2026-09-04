"""CLI entry point for argocheck."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import click

from .display import print_error, render_app_yaml, render_tree
from .helm import HelmError, check_helm
from .models import AppNode
from .parser import ParseError, load_yaml_file, parse_application
from .valuetree import ValueTreeError, build_leaf_node, matches_selection, parse_leaves
from .walker import walk


@click.command()
@click.argument("root_app", metavar="ROOT_APP", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--expand",
    "expand_apps",
    multiple=True,
    metavar="APP_NAME",
    help="Expand manifests for the named application(s) in the tree view.",
)
@click.option(
    "--show",
    metavar="APP_NAME",
    default=None,
    help="Print full YAML of all manifests rendered by the named application.",
)
@click.option(
    "--argocd-env",
    is_flag=True,
    default=False,
    help="Inject dummy ARGOCD_APP_* values into helm template calls.",
)
@click.option(
    "--max-depth",
    default=10,
    show_default=True,
    help="Maximum app-of-apps recursion depth.",
)
@click.option(
    "--env-map",
    "env_map",
    metavar="PATH",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional value-tree file (see README) that fans ROOT_APP out across "
         "a nested value map (e.g. cluster -> namespace) instead of rendering it once.",
)
@click.option(
    "--select",
    "selections",
    multiple=True,
    metavar="PATH",
    help="Only with --env-map: render only leaves under this path "
         "(e.g. --select clustername1 or --select clustername1/namespace1). "
         "Repeatable. Defaults to every leaf.",
)
@click.version_option(package_name="argocheck")
def main(
    root_app: Path,
    expand_apps: tuple[str, ...],
    show: str | None,
    argocd_env: bool,
    max_depth: int,
    env_map: Path | None,
    selections: tuple[str, ...],
) -> None:
    """Validate and dry-run an ArgoCD app-of-apps Helm structure locally.

    ROOT_APP is the path to a YAML file containing the root kind:Application manifest.
    """
    if selections and not env_map:
        print_error("--select requires --env-map.")
        sys.exit(1)

    # Verify helm is available
    try:
        version = check_helm()
        click.echo(f"Using {version}", err=True)
    except HelmError as e:
        print_error(str(e))
        sys.exit(1)

    try:
        doc = load_yaml_file(root_app)
        root_node = parse_application(doc)
    except ParseError as e:
        print_error(str(e))
        sys.exit(1)

    root_dir = root_app.resolve().parent

    leaf_nodes: list[AppNode] | None = None
    if env_map:
        try:
            env_doc = load_yaml_file(env_map)
            leaves = parse_leaves(env_doc)
            if selections:
                leaves = [leaf for leaf in leaves if any(matches_selection(leaf, s) for s in selections)]
                if not leaves:
                    raise ValueTreeError(f"No leaves matched --select {list(selections)!r}")
        except (ParseError, ValueTreeError) as e:
            print_error(str(e))
            sys.exit(1)
        leaf_nodes = [build_leaf_node(root_node, leaf) for leaf in leaves]
        root_node.children = leaf_nodes

    # Walk the tree inside a single temp directory
    # Resolve relative repoURLs in the root app relative to the manifest file's directory
    with tempfile.TemporaryDirectory(prefix="argocheck-") as tmp:
        tmp_dir = Path(tmp)
        if leaf_nodes is not None:
            for leaf_node in leaf_nodes:
                walk(
                    leaf_node,
                    tmp_dir=tmp_dir,
                    argocd_env=argocd_env,
                    max_depth=max_depth,
                    _parent_chart_dir=root_dir,
                )
        else:
            root_node = walk(
                root_node,
                tmp_dir=tmp_dir,
                argocd_env=argocd_env,
                max_depth=max_depth,
                _parent_chart_dir=root_dir,
            )

    # Output
    if show:
        found = render_app_yaml(root_node, show)
        if not found:
            print_error(f"Application {show!r} not found in the rendered tree.")
            sys.exit(1)
    else:
        render_tree(root_node, expand=set(expand_apps))

    # Exit non-zero if any node has an error
    if _has_error(root_node):
        sys.exit(1)


def _has_error(node: AppNode) -> bool:
    if node.error:
        return True
    return any(_has_error(c) for c in node.children)


if __name__ == "__main__":
    main()
