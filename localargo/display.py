"""Rich-based display of the AppNode tree."""
from __future__ import annotations

from collections import Counter
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.tree import Tree

from .models import AppNode

console = Console()


def _status_label(node: AppNode) -> Text:
    if node.error:
        label = Text()
        label.append("✗ ", style="bold red")
        label.append(node.name, style="bold white")
        label.append(f" [{node.namespace}]", style="dim")
        return label

    count = len(node.manifests)
    kinds = Counter(m.get("kind", "?") for m in node.manifests)
    summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))

    label = Text()
    label.append("✓ ", style="bold green")
    label.append(node.name, style="bold white")
    label.append(f" [{node.namespace}]", style="dim")
    if summary:
        label.append(f"  —  {summary}", style="cyan")
    else:
        label.append("  —  0 resources", style="dim")
    return label


def _add_node(tree: Tree, node: AppNode, expand: set[str]) -> None:
    branch = tree.add(_status_label(node))

    if node.error:
        err_text = Text()
        err_text.append(f"Error: {node.error}", style="red")
        branch.add(err_text)
        if hasattr(node.error, "cmd") and node.error.cmd:
            cmd_str = " ".join(str(c) for c in node.error.cmd)
            branch.add(Text(f"Command: {cmd_str}", style="dim red"))
        if hasattr(node.error, "stderr") and node.error.stderr:
            branch.add(Text(node.error.stderr.strip(), style="dim red"))
        return

    if node.name in expand:
        for manifest in node.manifests:
            kind = manifest.get("kind", "?")
            name = (manifest.get("metadata") or {}).get("name", "?")
            yaml_str = yaml.dump(manifest, default_flow_style=False, allow_unicode=True)
            syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False)
            branch.add(Panel(syntax, title=f"{kind}/{name}", border_style="dim"))

    for child in node.children:
        _add_node(branch, child, expand)


def render_tree(root: AppNode, expand: set[str] | None = None) -> None:
    expand = expand or set()
    tree = Tree(_status_label(root))
    if root.error:
        err_text = Text(f"Error: {root.error}", style="red")
        tree.add(err_text)
        if hasattr(root.error, "cmd") and root.error.cmd:
            tree.add(Text(" ".join(str(c) for c in root.error.cmd), style="dim red"))
        if hasattr(root.error, "stderr") and root.error.stderr:
            tree.add(Text(root.error.stderr.strip(), style="dim red"))
    else:
        if root.name in expand:
            for manifest in root.manifests:
                kind = manifest.get("kind", "?")
                name = (manifest.get("metadata") or {}).get("name", "?")
                yaml_str = yaml.dump(manifest, default_flow_style=False, allow_unicode=True)
                syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False)
                tree.add(Panel(syntax, title=f"{kind}/{name}", border_style="dim"))

        for child in root.children:
            _add_node(tree, child, expand)

    console.print(tree)


def render_app_yaml(node: AppNode, app_name: str) -> bool:
    """Print the raw YAML of all manifests in the named app. Returns True if found."""
    if node.name == app_name:
        if node.error:
            console.print(f"[red]Application {app_name!r} has an error: {node.error}[/red]")
            return True
        for manifest in node.manifests:
            yaml_str = yaml.dump(manifest, default_flow_style=False, allow_unicode=True)
            syntax = Syntax(yaml_str, "yaml", theme="monokai")
            console.print(syntax)
            console.print("---")
        return True

    for child in node.children:
        if render_app_yaml(child, app_name):
            return True
    return False


def print_error(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")
