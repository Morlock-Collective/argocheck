"use strict";
const { createApp, ref, computed, watch, onMounted } = Vue;

// ── Utilities ─────────────────────────────────────────────────────────────

function basename(p) { return p.split("/").pop() || p; }
function dirname(p)  { const parts = p.split("/"); parts.pop(); return parts.join("/") || "/"; }

function flattenTree(node, depth = 0) {
  const out = [{ depth, node }];
  for (const c of (node.children || [])) out.push(...flattenTree(c, depth + 1));
  return out;
}

function computeTreePrefixes(flat) {
  const n = flat.length;
  const NB = " "; // non-breaking space — won't collapse in HTML

  const isLast = flat.map((_, i) => {
    const d = flat[i].depth;
    for (let j = i + 1; j < n; j++) {
      if (flat[j].depth <= d) return flat[j].depth < d;
    }
    return true;
  });

  return flat.map(({ depth }, i) => {
    if (depth === 0) return "";
    const parts = [];
    for (let col = 1; col < depth; col++) {
      let ancLast = true;
      for (let k = i - 1; k >= 0; k--) {
        if (flat[k].depth === col) { ancLast = isLast[k]; break; }
      }
      parts.push(ancLast ? `${NB}${NB}${NB}${NB}` : `│${NB}${NB}${NB}`);
    }
    parts.push(isLast[i] ? `└─${NB}` : `├─${NB}`);
    return parts.join("");
  });
}

function findNode(flat, name) {
  return flat.find(({ node }) => node.name === name)?.node ?? null;
}

// Read/write navigation state via query parameters (hash is left free for
// the browser's built-in anchor navigation).
function updateQueryParams(updates) {
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, value);
  }
  const qs = params.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  history.replaceState(null, "", url);
}

function highlightYaml(obj) {
  const str = jsyaml.dump(obj, { indent: 2, noRefs: true, lineWidth: -1 });
  return hljs.highlight(str, { language: "yaml" }).value;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  return r.json();
}

// ── YamlBlock component ───────────────────────────────────────────────────

const YamlBlock = {
  props: { content: { required: true } },
  data() { return { copied: false }; },
  computed: {
    yamlStr() { return jsyaml.dump(this.content, { indent: 2, noRefs: true, lineWidth: -1 }); },
    html()    { return hljs.highlight(this.yamlStr, { language: "yaml" }).value; },
  },
  methods: {
    async copy() {
      await navigator.clipboard.writeText(this.yamlStr);
      this.copied = true;
      setTimeout(() => { this.copied = false; }, 1500);
    }
  },
  template: `
    <div class="yaml-wrapper">
      <button class="copy-btn" @click="copy">{{ copied ? "✓ Copied" : "Copy" }}</button>
      <pre class="hljs yaml-standalone"><code v-html="html"></code></pre>
    </div>
  `
};

// ── ResourceViewer component ──────────────────────────────────────────────

const ResourceViewer = {
  components: { YamlBlock },
  props: {
    manifests:   { type: Array,  required: true },
    displayMode: { type: String, default: "tabs" },
    expandSeq:   { type: Number, default: 0 },
    collapseSeq: { type: Number, default: 0 },
    initialActiveKind: { type: String, default: null },
    initialOpenKeys:   { type: Array,  default: () => [] },
  },
  emits: ["state-change"],
  setup(props, { emit }) {
    const kindsInit = new Set(props.manifests.map(m => m.kind ?? "?"));
    const keysInit  = new Set(props.manifests.map(m => `${m.kind}/${m.metadata?.name ?? "?"}`));

    const activeKind = ref(
      props.initialActiveKind && kindsInit.has(props.initialActiveKind) ? props.initialActiveKind : null
    );
    const openResources = ref(new Set(props.initialOpenKeys.filter(k => keysInit.has(k))));

    const kinds = computed(() => [...new Set(props.manifests.map(m => m.kind ?? "?"))].sort());
    const currentKind = computed(() => activeKind.value ?? kinds.value[0] ?? null);
    const activeManifests = computed(() =>
      props.manifests.filter(m => (m.kind ?? "?") === currentKind.value)
    );
    const manifestsByKind = computed(() => {
      const map = Object.fromEntries(kinds.value.map(k => [k, []]));
      for (const m of props.manifests) (map[m.kind ?? "?"] ??= []).push(m);
      for (const k in map) map[k].sort((a, b) => resName(a).localeCompare(resName(b)));
      return map;
    });

    function resKey(m) { return `${m.kind}/${m.metadata?.name ?? "?"}`; }
    function resName(m) { return m.metadata?.name ?? "?"; }
    function toggleRes(key) {
      const s = new Set(openResources.value);
      s.has(key) ? s.delete(key) : s.add(key);
      openResources.value = s;
    }
    function isOpen(key) { return openResources.value.has(key); }

    watch(() => props.manifests,   () => { activeKind.value = null; openResources.value = new Set(); });
    watch(() => props.expandSeq,   () => { openResources.value = new Set(props.manifests.map(resKey)); });
    watch(() => props.collapseSeq, () => { openResources.value = new Set(); });

    watch([activeKind, openResources], () => {
      emit("state-change", { kind: currentKind.value, open: [...openResources.value] });
    });

    return { kinds, currentKind, activeManifests, manifestsByKind, activeKind, resKey, resName, toggleRes, isOpen };
  },
  template: `
    <div>
      <div v-if="manifests.length === 0" style="color:var(--text-muted);font-size:0.85rem;">
        No non-Application resources rendered by this app.
      </div>

      <!-- ── List mode: kinds as headings, all resources visible ── -->
      <template v-else-if="displayMode === 'list'">
        <div v-for="kind in kinds" :key="kind" class="kind-group">
          <div class="kind-heading">{{ kind }}</div>
          <div v-for="m in manifestsByKind[kind]" :key="resKey(m)" class="resource-item">
            <div class="resource-item-header" @click="toggleRes(resKey(m))">
              <span>{{ resName(m) }}</span>
              <i class="resource-chevron" :class="{open: isOpen(resKey(m))}">›</i>
            </div>
            <div v-if="isOpen(resKey(m))" class="resource-yaml">
              <yaml-block :content="m"></yaml-block>
            </div>
          </div>
        </div>
      </template>

      <!-- ── Tabs mode (default): one kind at a time ── -->
      <template v-else>
        <div class="kind-tabs">
          <button v-for="k in kinds" :key="k" class="kind-tab"
                  :class="{active: k === currentKind}"
                  @click="activeKind = k">{{ k }}</button>
        </div>
        <div v-for="m in activeManifests" :key="resKey(m)" class="resource-item">
          <div class="resource-item-header" @click="toggleRes(resKey(m))">
            <span>{{ resName(m) }}</span>
            <i class="resource-chevron" :class="{open: isOpen(resKey(m))}">›</i>
          </div>
          <div v-if="isOpen(resKey(m))" class="resource-yaml">
            <yaml-block :content="m"></yaml-block>
          </div>
        </div>
      </template>
    </div>
  `
};

// ── AppDetail component ───────────────────────────────────────────────────

const AppDetail = {
  components: { YamlBlock, ResourceViewer },
  props: {
    node:        { type: Object, required: true },
    displayMode: { type: String, default: "tabs" },
    expandSeq:   { type: Number, default: 0 },
    collapseSeq: { type: Number, default: 0 },
    initialActiveKind: { type: String, default: null },
    initialOpenKeys:   { type: Array,  default: () => [] },
  },
  emits: ["selectApp", "stateChange"],
  setup(props) {
    const showYaml = ref(false);
    const sourceOpen = ref(false);
    watch(() => props.node.name, () => { showYaml.value = false; sourceOpen.value = false; });

    const sourceLabel = computed(() =>
      props.node.isMultiSource ? `Sources (${props.node.sources.length})` : "Source"
    );
    function sourceRows(src) {
      const rows = [["repoURL", src.repoURL]];
      if (src.chart) rows.push(["chart", src.chart]);
      if (src.path) rows.push(["path", src.path]);
      rows.push(["targetRevision", src.targetRevision]);
      if (src.releaseName) rows.push(["releaseName", src.releaseName]);
      return rows;
    }
    return { showYaml, sourceOpen, sourceLabel, sourceRows };
  },
  template: `
    <div>
      <!-- Header -->
      <div class="detail-header">
        <div class="detail-title">
          <h2>{{ node.name }}</h2>
          <div class="ns">namespace: {{ node.namespace }}</div>
        </div>
        <div class="detail-actions">
          <button v-if="node.appManifest" class="btn btn-sm"
                  @click="showYaml = !showYaml">
            {{ showYaml ? "Compact view" : "Application YAML" }}
          </button>
          <span class="status-badge" :class="node.error ? 'err' : 'ok'">
            {{ node.error ? "❌ Failed" : "✅ OK" }}
          </span>
        </div>
      </div>

      <!-- Application YAML view -->
      <yaml-block v-if="showYaml && node.appManifest" :content="node.appManifest"></yaml-block>

      <!-- Compact view -->
      <template v-else>

        <!-- Error -->
        <div v-if="node.error" class="error-panel">
          <div class="error-msg">{{ node.error.message }}</div>
          <template v-if="node.error.cmd">
            <div class="error-block-label">Command</div>
            <div class="cmd-block">{{ node.error.cmd }}</div>
          </template>
          <template v-if="node.error.stderr">
            <div class="error-block-label">Output</div>
            <div class="stderr-block">{{ node.error.stderr }}</div>
          </template>
        </div>

        <template v-else>
          <!-- Sources (collapsible) -->
          <div class="collapsible">
            <button class="collapsible-header" @click="sourceOpen = !sourceOpen">
              <i>{{ sourceOpen ? "▾" : "▸" }}</i> {{ sourceLabel }}
            </button>
            <div v-if="sourceOpen" class="collapsible-body">
              <div v-for="(src, i) in node.sources" :key="i" class="source-block">
                <div v-if="node.isMultiSource" class="source-num">
                  Source {{ i + 1 }}{{ src.ref ? " — ref: " + src.ref : "" }}
                </div>
                <table class="info-table">
                  <tr v-for="[k,v] in sourceRows(src)" :key="k">
                    <td>{{ k }}</td><td>{{ v }}</td>
                  </tr>
                </table>
              </div>
            </div>
          </div>

          <!-- Child apps -->
          <div v-if="node.children && node.children.length" class="children-info">
            Child applications:
            <button v-for="c in node.children" :key="c.name"
                    class="child-tag" @click="$emit('selectApp', c.name)">{{ c.name }}</button>
          </div>

          <!-- Resources -->
          <div class="resources-header">
            Resources ({{ (node.manifests || []).length }})
          </div>
          <resource-viewer :manifests="node.manifests || []"
                           :display-mode="displayMode"
                           :expand-seq="expandSeq"
                           :collapse-seq="collapseSeq"
                           :initial-active-kind="initialActiveKind"
                           :initial-open-keys="initialOpenKeys"
                           @state-change="$emit('stateChange', $event)"></resource-viewer>
        </template>

      </template>
    </div>
  `
};

// ── FileBrowser component ─────────────────────────────────────────────────

const FileBrowser = {
  props: { seedPath: { type: String, default: "" } },
  emits: ["select"],
  setup(props, { emit }) {
    const contents = ref({ current: "", parent: null, dirs: [], files: [], error: null });

    async function browseTo(path) {
      contents.value = await api("GET", `/api/browse?path=${encodeURIComponent(path)}`);
    }

    onMounted(() => {
      const seed = props.seedPath ? dirname(props.seedPath) : "~";
      browseTo(seed);
    });

    watch(() => props.seedPath, (v) => { if (v) browseTo(dirname(v)); });

    function selectFile(f) {
      emit("select", f);
    }

    return { contents, browseTo, selectFile };
  },
  template: `
    <div>
      <div class="browser-path">{{ contents.current }}</div>
      <button v-if="contents.parent" class="btn btn-ghost browser-item"
              @click="browseTo(contents.parent)">↑ ..</button>
      <div v-if="contents.error" style="color:var(--error);font-size:0.8rem;">{{ contents.error }}</div>
      <template v-for="d in contents.dirs" :key="d">
        <button class="btn btn-ghost browser-item" @click="browseTo(d)" :title="d">
          📁 {{ d.split('/').pop() }}
        </button>
      </template>
      <div v-if="contents.files && contents.files.length" class="browser-divider"></div>
      <template v-for="f in contents.files" :key="f">
        <button class="btn btn-ghost browser-item"
                :class="{ 'is-selected': f === seedPath }"
                @click="selectFile(f)" :title="f">
          📄 {{ f.split('/').pop() }}
        </button>
      </template>
      <div v-if="!contents.dirs?.length && !contents.files?.length && !contents.error"
           style="color:var(--text-muted);font-size:0.82rem;">
        No directories or YAML files here.
      </div>
    </div>
  `
};

// ── Root App ──────────────────────────────────────────────────────────────

createApp({
  components: { FileBrowser, AppDetail },

  setup() {
    // ── State
    const rootPath    = ref("");
    const recents     = ref([]);
    const isRendering = ref(false);
    const renderResult = ref(null);   // { ok, tree, error }
    const topError    = ref(null);
    const selectedApp = ref(null);
    const options     = ref({ argocdEnv: false, maxDepth: 10 });

    // ── Navigation state (recorded in the URL query string)
    const viewState     = ref({ kind: null, open: [] }); // resource-viewer state for selectedApp
    const staleApp      = ref(null);                     // app name from URL/prior view that no longer exists
    const staleSelection = ref(null);                    // { app, kind, open } to retry on the next render
    const pendingSelection = ref(null);                  // selection to apply once the next render completes

    // Sidebar section open/closed
    const sections = ref({ recents: true, browser: false, options: false, display: false });

    // ── Display controls
    const displayMode = ref(localStorage.getItem("displayMode") || "tabs");
    const expandSeq   = ref(0);
    const collapseSeq = ref(0);
    watch(displayMode, v => localStorage.setItem("displayMode", v));
    function expandAll()   { expandSeq.value++;   }
    function collapseAll() { collapseSeq.value++; }

    // ── Sidebar resize
    const MIN_W = 180, MAX_W = 600;
    const sidebarWidth = ref(parseInt(localStorage.getItem("sidebarWidth")) || 300);

    function onHandleMouseDown(e) {
      e.preventDefault();
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";

      function onMove(e) {
        sidebarWidth.value = Math.max(MIN_W, Math.min(MAX_W, e.clientX));
      }
      function onUp() {
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        localStorage.setItem("sidebarWidth", sidebarWidth.value);
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    }

    // ── Derived
    const flat = computed(() =>
      renderResult.value?.tree ? flattenTree(renderResult.value.tree) : []
    );
    const prefixes = computed(() => computeTreePrefixes(flat.value));

    const selectedNode = computed(() => {
      if (!flat.value.length) return null;
      if (selectedApp.value) {
        const found = findNode(flat.value, selectedApp.value);
        if (found) return found;
      }
      return flat.value[0]?.node ?? null;
    });

    const totalApps      = computed(() => flat.value.length);
    const totalResources = computed(() => flat.value.reduce((s, { node }) => s + (node.manifests?.length ?? 0), 0));
    const totalErrors    = computed(() => flat.value.filter(({ node }) => node.error).length);

    // ── Actions
    async function loadRecents() {
      recents.value = await api("GET", "/api/recents");
    }

    async function removeRecent(path) {
      await api("DELETE", `/api/recents?path=${encodeURIComponent(path)}`);
      await loadRecents();
    }

    function selectPath(path) {
      rootPath.value = path;
      sections.value.recents = false;
      sections.value.browser = false;
    }

    function onBrowserSelect(path) {
      rootPath.value = path;
      sections.value.browser = false;
    }

    // Apply a remembered/requested selection to a freshly rendered tree.
    // Falls back to the root app and records `target` as stale if it's gone,
    // keeping it around so a subsequent re-render can retry it.
    function applySelection(flatList, rootName, target, kind, open) {
      staleApp.value = null;
      staleSelection.value = null;
      if (target && target !== rootName) {
        if (findNode(flatList, target)) {
          selectedApp.value = target;
          viewState.value = { kind: kind || null, open: open || [] };
          return;
        }
        staleApp.value = target;
        staleSelection.value = { app: target, kind: kind || null, open: open || [] };
        selectedApp.value = rootName;
        viewState.value = { kind: null, open: [] };
        return;
      }
      selectedApp.value = rootName;
      viewState.value = { kind: kind || null, open: open || [] };
    }

    // Persist the current navigation state (root path, selected app, and
    // resource-viewer state) to the URL's query parameters. While an app
    // is stale, its reference is kept in the URL so a re-render can retry it.
    function syncUrl() {
      const rootName = flat.value[0]?.node.name ?? null;
      let appParam = null, kind = null, open = null;
      if (staleSelection.value) {
        ({ app: appParam, kind, open } = staleSelection.value);
      } else if (renderResult.value && selectedApp.value && selectedApp.value !== rootName) {
        appParam = selectedApp.value;
        ({ kind, open } = viewState.value);
      }
      updateQueryParams({
        path: rootPath.value.trim() || null,
        app: appParam,
        kind: kind || null,
        open: (open && open.length) ? open.join(",") : null,
      });
    }

    function selectApp(name) {
      if (selectedApp.value === name) return;
      selectedApp.value = name;
      viewState.value = { kind: null, open: [] };
      staleApp.value = null;
      staleSelection.value = null;
      syncUrl();
    }

    function onResourceStateChange(state) {
      viewState.value = state;
      syncUrl();
    }

    function clearNavigation() {
      staleApp.value = null;
      staleSelection.value = null;
      syncUrl();
    }

    async function doRender() {
      if (!rootPath.value.trim() || isRendering.value) return;
      isRendering.value = true;
      topError.value = null;
      const previousApp  = selectedApp.value;
      const previousView = viewState.value;
      renderResult.value = null;
      selectedApp.value = null;
      staleApp.value = null;
      try {
        const result = await api("POST", "/api/render", {
          path: rootPath.value.trim(),
          argocd_env: options.value.argocdEnv,
          max_depth: options.value.maxDepth,
        });
        if (!result.ok) {
          topError.value = result.error;
        } else {
          renderResult.value = result;
          const flatList = flattenTree(result.tree);
          const rootName = result.tree?.name ?? null;
          if (pendingSelection.value) {
            const sel = pendingSelection.value;
            pendingSelection.value = null;
            applySelection(flatList, rootName, sel.app, sel.kind, sel.open);
          } else if (staleSelection.value) {
            const sel = staleSelection.value;
            applySelection(flatList, rootName, sel.app, sel.kind, sel.open);
          } else {
            applySelection(flatList, rootName, previousApp, previousView.kind, previousView.open);
          }
          await loadRecents();
        }
      } catch (e) {
        topError.value = String(e);
      } finally {
        isRendering.value = false;
        syncUrl();
      }
    }

    onMounted(() => {
      loadRecents();
      const params = new URLSearchParams(window.location.search);
      const path = params.get("path");
      if (path) {
        rootPath.value = path;
        pendingSelection.value = {
          app: params.get("app") || null,
          kind: params.get("kind") || null,
          open: (params.get("open") || "").split(",").filter(Boolean),
        };
        doRender();
      }
    });

    return {
      rootPath, recents, isRendering, renderResult, topError,
      selectedApp, options, sections,
      flat, prefixes, selectedNode,
      totalApps, totalResources, totalErrors,
      loadRecents, removeRecent, selectPath, onBrowserSelect, doRender,
      basename, dirname,
      sidebarWidth, onHandleMouseDown,
      displayMode, expandSeq, collapseSeq, expandAll, collapseAll,
      viewState, staleApp, selectApp, onResourceStateChange, clearNavigation,
    };
  },

  template: `
    <div class="layout">

      <!-- ── Sidebar ── -->
      <aside class="sidebar" :style="{ width: sidebarWidth + 'px' }">
        <div class="sidebar-header">
          <span class="logo-icon">⎈</span>
          <span class="logo">localargo</span>
        </div>

        <!-- Path input -->
        <div class="path-section">
          <label class="path-label">Root Application manifest</label>
          <input class="path-input" v-model="rootPath"
                 placeholder="/path/to/root-app.yaml"
                 @keyup.enter="doRender">
        </div>

        <!-- Recent files -->
        <div class="sidebar-section" v-if="recents.length">
          <button class="section-header" @click="sections.recents = !sections.recents">
            Recent ({{ recents.length }})
            <i class="section-chevron" :class="{open: sections.recents}">›</i>
          </button>
          <div v-if="sections.recents" class="section-body">
            <div v-for="p in recents" :key="p" class="recent-item">
              <button class="btn btn-ghost recent-pick" @click="selectPath(p)" :title="p">
                <div class="recent-name">📄 {{ basename(p) }}</div>
                <div class="recent-dir">{{ dirname(p) }}</div>
              </button>
              <button class="btn-icon" @click="removeRecent(p)" title="Remove">✕</button>
            </div>
          </div>
        </div>

        <!-- File browser -->
        <div class="sidebar-section">
          <button class="section-header" @click="sections.browser = !sections.browser">
            Browse…
            <i class="section-chevron" :class="{open: sections.browser}">›</i>
          </button>
          <div v-if="sections.browser" class="section-body">
            <file-browser :seed-path="rootPath" @select="onBrowserSelect"></file-browser>
          </div>
        </div>

        <!-- Options -->
        <div class="sidebar-section">
          <button class="section-header" @click="sections.options = !sections.options">
            Options
            <i class="section-chevron" :class="{open: sections.options}">›</i>
          </button>
          <div v-if="sections.options" class="section-body">
            <div class="option-row">
              <input type="checkbox" v-model="options.argocdEnv" id="opt-env">
              <label for="opt-env">Inject ARGOCD_APP_* dummy values</label>
            </div>
            <div class="option-row">
              <label for="opt-depth">Max recursion depth</label>
              <input id="opt-depth" type="number" v-model.number="options.maxDepth" min="1" max="50">
            </div>
          </div>
        </div>

        <!-- Render button -->
        <button class="btn btn-primary render-btn"
                @click="doRender"
                :disabled="isRendering || !rootPath.trim()">
          {{ isRendering ? "Rendering…" : "Render" }}
        </button>

        <!-- Display controls (shown after a render) -->
        <div class="sidebar-section" v-if="renderResult">
          <button class="section-header" @click="sections.display = !sections.display">
            Display
            <i class="section-chevron" :class="{open: sections.display}">›</i>
          </button>
          <div v-if="sections.display" class="section-body">
            <div class="option-row">
              <label>Mode</label>
              <div class="view-toggle">
                <button class="btn btn-sm" :class="{'btn-primary': displayMode === 'tabs'}"
                        @click="displayMode = 'tabs'">Tabs</button>
                <button class="btn btn-sm" :class="{'btn-primary': displayMode === 'list'}"
                        @click="displayMode = 'list'">List</button>
              </div>
            </div>
            <div class="option-row">
              <button class="btn btn-sm" style="flex:1;justify-content:center" @click="expandAll()">Expand all</button>
              <button class="btn btn-sm" style="flex:1;justify-content:center" @click="collapseAll()">Collapse all</button>
            </div>
          </div>
        </div>

        <!-- App tree -->
        <div v-if="flat.length" class="tree-section">
          <div class="tree-label">Applications</div>
          <button v-for="({depth, node}, i) in flat" :key="node.name"
                  class="tree-item"
                  :class="{active: node.name === (selectedNode && selectedNode.name)}"
                  @click="selectApp(node.name)">
            <span class="tree-prefix">{{ prefixes[i] }}</span>
            <span>{{ node.error ? "❌" : "✅" }}</span>
            <span class="tree-name">{{ node.name }}</span>
          </button>
        </div>
      </aside>

      <div class="resize-handle" @mousedown="onHandleMouseDown"></div>

      <!-- ── Main content ── -->
      <main class="main">

        <div v-if="isRendering" class="loading">
          <div class="spinner"></div>
          <p>Running helm template…</p>
        </div>

        <div v-else-if="topError" class="top-error">
          <h3>Error</h3>
          <p>{{ topError }}</p>
        </div>

        <div v-else-if="!renderResult" class="welcome">
          <div style="font-size:2.5rem">⎈</div>
          <h2>Getting started</h2>
          <p>Select a root <code>kind:Application</code> manifest using Browse… or by typing the path, then click <strong>Render</strong>.</p>
          <p style="color:var(--text-muted)">Requires <code>helm</code> on your PATH.</p>
        </div>

        <template v-else>
          <div class="metrics">
            <div class="metric">
              <div class="metric-value">{{ totalApps }}</div>
              <div class="metric-label">Applications</div>
            </div>
            <div class="metric">
              <div class="metric-value">{{ totalResources }}</div>
              <div class="metric-label">Resources</div>
            </div>
            <div class="metric" :class="{'has-errors': totalErrors > 0}">
              <div class="metric-value">{{ totalErrors }}</div>
              <div class="metric-label">Errors</div>
            </div>
          </div>
          <div v-if="staleApp" class="stale-warning">
            <span>⚠ Application "{{ staleApp }}" is no longer present in the rendered tree.</span>
            <button class="btn btn-sm" @click="clearNavigation">Clear navigation</button>
          </div>
          <div class="divider"></div>
          <app-detail v-if="selectedNode" :node="selectedNode" :key="selectedNode.name"
                      :display-mode="displayMode"
                      :expand-seq="expandSeq"
                      :collapse-seq="collapseSeq"
                      :initial-active-kind="viewState.kind"
                      :initial-open-keys="viewState.open"
                      @select-app="selectApp"
                      @state-change="onResourceStateChange"></app-detail>
        </template>

      </main>
    </div>
  `
}).mount("#app");
