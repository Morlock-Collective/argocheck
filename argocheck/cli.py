"""CLI entry point for argocheck."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import click

from .display import print_error, render_app_yaml, render_tree
from .helm import HelmError, check_helm
from .parser import ParseError, load_yaml_file, parse_application
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
@click.version_option(package_name="argocheck")
def main(
    root_app: Path,
    expand_apps: tuple[str, ...],
    show: str | None,
    argocd_env: bool,
    max_depth: int,
) -> None:
    """Validate and dry-run an ArgoCD app-of-apps Helm structure locally.

    ROOT_APP is the path to a YAML file containing the root kind:Application manifest.
    """
    # Verify helm is available
    try:
        version = check_helm()
        click.echo(f"Using {version}", err=True)
    except HelmError as e:
        print_error(str(e))
        sys.exit(1)

    # Parse the root application
    try:
        doc = load_yaml_file(root_app)
        root_node = parse_application(doc)
    except ParseError as e:
        print_error(str(e))
        sys.exit(1)

    # Walk the tree inside a single temp directory
    # Resolve relative repoURLs in the root app relative to the manifest file's directory
    root_dir = root_app.resolve().parent
    with tempfile.TemporaryDirectory(prefix="argocheck-") as tmp:
        tmp_dir = Path(tmp)
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


def _has_error(node: "AppNode") -> bool:  # type: ignore[name-defined]
    from .models import AppNode
    if node.error:
        return True
    return any(_has_error(c) for c in node.children)
