"""Streamlit web interface for localargo."""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from .helm import HelmError, check_helm
from .models import AppNode
from .parser import ParseError, load_yaml_file, parse_application
from .walker import walk

# ── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="localargo",
    page_icon="⎈",
    layout="wide",
    initial_sidebar_state="expanded",
)

_KEY_TREE = "tree"
_KEY_SELECTED = "selected_app"
_KEY_ERROR = "top_error"


# ── Pure helpers (defined before rendering code) ─────────────────────────────

def _flatten(node: AppNode, depth: int = 0) -> list[tuple[int, AppNode]]:
    result = [(depth, node)]
    for child in node.children:
        result.extend(_flatten(child, depth + 1))
    return result


def _status_icon(node: AppNode) -> str:
    return "✗" if node.error else "✓"


def _run_rendering(
    root_app_path: Path,
    argocd_env: bool,
    max_depth: int,
) -> tuple[AppNode | None, str | None]:
    try:
        check_helm()
    except HelmError as e:
        return None, str(e)

    try:
        doc = load_yaml_file(root_app_path)
        root_node = parse_application(doc)
    except ParseError as e:
        return None, str(e)

    root_dir = root_app_path.resolve().parent
    with tempfile.TemporaryDirectory(prefix="localargo-") as tmp:
        root_node = walk(
            root_node,
            tmp_dir=Path(tmp),
            argocd_env=argocd_env,
            max_depth=max_depth,
            _parent_chart_dir=root_dir,
        )

    return root_node, None


def _render_app_detail(node: AppNode) -> None:
    """Render the detail panel for a selected AppNode."""
    col_title, col_status = st.columns([5, 1])
    with col_title:
        st.subheader(node.name)
        st.caption(f"namespace: `{node.namespace}`")
    with col_status:
        st.error("Failed") if node.error else st.success("OK")

    if node.error:
        st.error(f"**Error:** {node.error}")
        err = node.error
        if hasattr(err, "cmd") and err.cmd:
            st.code(" ".join(str(c) for c in err.cmd), language="bash")
        if hasattr(err, "stderr") and err.stderr:
            st.code(err.stderr.strip(), language="text")
        return

    # Source(s) info
    label = f"Sources ({len(node.sources)})" if node.is_multi_source else "Source"
    with st.expander(label):
        for i, src in enumerate(node.sources):
            if node.is_multi_source:
                st.markdown(f"**Source {i + 1}**" + (f" — ref: `{src.ref}`" if src.ref else ""))
            info: dict[str, Any] = {"repoURL": src.repo_url}
            if src.ref:
                info["ref"] = src.ref
            if src.chart:
                info["chart"] = src.chart
            if src.path:
                info["path"] = src.path
            info["targetRevision"] = src.target_revision
            if src.release_name:
                info["releaseName"] = src.release_name
            if i == 0 and node.chart_dir:
                info["resolvedChartDir"] = str(node.chart_dir)
            st.code(yaml.dump(info, default_flow_style=False), language="yaml")

    # Child application links
    if node.children:
        child_names = ", ".join(c.name for c in node.children)
        st.info(f"**Child applications:** {child_names}")

    # Resources
    st.markdown(f"**Resources** ({len(node.manifests)})")

    if not node.manifests:
        st.caption("No non-Application resources rendered by this app.")
        return

    # Group resources by kind into tabs (up to 10 kinds); fall back to selectbox
    kinds = sorted({m.get("kind", "?") for m in node.manifests})

    if len(kinds) <= 10:
        tabs = st.tabs(kinds)
        kind_to_tab = dict(zip(kinds, tabs))
        for manifest in node.manifests:
            kind = manifest.get("kind", "?")
            name = (manifest.get("metadata") or {}).get("name", "?")
            yaml_str = yaml.dump(manifest, default_flow_style=False, allow_unicode=True)
            with kind_to_tab[kind]:
                with st.expander(f"{kind}/{name}", expanded=False):
                    st.code(yaml_str, language="yaml")
    else:
        options = [
            f"{m.get('kind','?')}/{(m.get('metadata') or {}).get('name','?')}"
            for m in node.manifests
        ]
        choice = st.selectbox("Resource", options)
        idx = options.index(choice)
        yaml_str = yaml.dump(node.manifests[idx], default_flow_style=False, allow_unicode=True)
        st.code(yaml_str, language="yaml")


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⎈ localargo")
    st.caption("Local ArgoCD app-of-apps validator")
    st.divider()

    root_app_input = st.text_input(
        "Root Application manifest",
        placeholder="/path/to/root-app.yaml",
        help="Path to a YAML file containing a kind:Application manifest.",
    )

    with st.expander("Options"):
        argocd_env = st.checkbox(
            "Inject ARGOCD_APP_* dummy values",
            help=(
                "Passes dummy ARGOCD_APP_* keys via --set so charts that "
                "reference them as Helm values don't fail."
            ),
        )
        max_depth = st.number_input(
            "Max recursion depth", min_value=1, max_value=50, value=10, step=1,
        )

    run_clicked = st.button("Render", type="primary", use_container_width=True)

    # App tree navigation (shown after a successful render)
    if _KEY_TREE in st.session_state and st.session_state[_KEY_TREE] is not None:
        st.divider()
        st.subheader("Applications")
        flat = _flatten(st.session_state[_KEY_TREE])
        for depth, node in flat:
            icon = _status_icon(node)
            indent = " " * (depth * 4)
            label = f"{indent}{icon} {node.name}"
            is_selected = st.session_state.get(_KEY_SELECTED) == node.name
            if st.button(
                label,
                key=f"nav_{node.name}",
                use_container_width=True,
                type="primary" if is_selected else "secondary",
            ):
                st.session_state[_KEY_SELECTED] = node.name
                st.rerun()

# ── Handle render button ─────────────────────────────────────────────────────

if run_clicked:
    if not root_app_input:
        st.session_state[_KEY_ERROR] = "Please enter a path to the root Application manifest."
        st.session_state.pop(_KEY_TREE, None)
    else:
        path = Path(root_app_input)
        if not path.exists():
            st.session_state[_KEY_ERROR] = f"File not found: {path}"
            st.session_state.pop(_KEY_TREE, None)
        else:
            with st.spinner("Running helm template…"):
                tree, err = _run_rendering(path, argocd_env=bool(argocd_env), max_depth=int(max_depth))
            if err:
                st.session_state[_KEY_ERROR] = err
                st.session_state.pop(_KEY_TREE, None)
            else:
                st.session_state[_KEY_TREE] = tree
                st.session_state.pop(_KEY_ERROR, None)
                initial_flat = _flatten(tree)
                st.session_state[_KEY_SELECTED] = initial_flat[0][1].name if initial_flat else None
        st.rerun()

# ── Main panel ───────────────────────────────────────────────────────────────

if _KEY_ERROR in st.session_state:
    st.error(st.session_state[_KEY_ERROR])

elif _KEY_TREE not in st.session_state:
    st.markdown(
        """
        ## Getting started

        Enter the path to your root ArgoCD `kind: Application` manifest in the
        sidebar and click **Render**.

        localargo will recursively run `helm template` for every Application in
        the hierarchy and display the resulting resource tree here.

        **Requirements:** `helm` must be on your `PATH`.
        """
    )

else:
    tree: AppNode = st.session_state[_KEY_TREE]
    flat = _flatten(tree)
    selected_name = st.session_state.get(_KEY_SELECTED)

    selected_node: AppNode | None = None
    for _, node in flat:
        if node.name == selected_name:
            selected_node = node
            break
    if selected_node is None and flat:
        selected_node = flat[0][1]

    # Summary metrics
    total_apps = len(flat)
    failed_apps = sum(1 for _, n in flat if n.error)
    total_resources = sum(len(n.manifests) for _, n in flat)

    c1, c2, c3 = st.columns(3)
    c1.metric("Applications", total_apps)
    c2.metric("Resources", total_resources)
    c3.metric("Errors", failed_apps)

    st.divider()

    if selected_node:
        _render_app_detail(selected_node)
