"use strict";
const { createApp, ref, computed, watch, onMounted } = Vue;

// ── Utilities ─────────────────────────────────────────────────────────────

function basename(p) { return p.split("/").pop() || p; }
function dirname(p)  { const parts = p.split("/"); parts.pop(); return parts.join("/") || "/"; }

// Each entry carries a `path` (names from root to this node) and a `pathKey`
// (path joined by "/"), which uniquely identify a node even when multiple
// leaves share the same name in different branches of the tree.
function flattenTree(node, depth = 0, parentPath = []) {
  const path = [...parentPath, node.name];
  const out = [{ depth, node, path, pathKey: path.join("/") }];
  for (const c of (node.children || [])) out.push(...flattenTree(c, depth + 1, path));
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

function findNode(flat, pathKey) {
  return flat.find((e) => e.pathKey === pathKey)?.node ?? null;
}

// ── Diff utilities ───────────────────────────────────────────────────────

function dumpYaml(obj) {
  return jsyaml.dump(obj, { indent: 2, noRefs: true, lineWidth: -1 });
}

function resourceDiffKey(m) {
  return `${m.kind ?? "?"}/${m.metadata?.name ?? "?"}`;
}

// LCS-based line diff: returns a list of {type: "eq"|"add"|"del", text}.
function diffLines(a, b) {
  const aLines = a.split("\n");
  const bLines = b.split("\n");
  const n = aLines.length, m = bLines.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = aLines[i] === bLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (aLines[i] === bLines[j]) { ops.push({ type: "eq", text: aLines[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: "del", text: aLines[i] }); i++; }
    else { ops.push({ type: "add", text: bLines[j] }); j++; }
  }
  while (i < n) { ops.push({ type: "del", text: aLines[i] }); i++; }
  while (j < m) { ops.push({ type: "add", text: bLines[j] }); j++; }
  return ops;
}

// Collapse runs of unchanged lines down to `context` lines around each
// change, returning a list of {type: "lines", ops} / {type: "collapsed", count}.
function collapseContext(ops, context = 3) {
  const n = ops.length;
  const ranges = [];
  for (let i = 0; i < n; i++) {
    if (ops[i].type === "eq") continue;
    const start = Math.max(0, i - context);
    const end = Math.min(n, i + context + 1);
    if (ranges.length && start <= ranges[ranges.length - 1][1]) {
      ranges[ranges.length - 1][1] = Math.max(ranges[ranges.length - 1][1], end);
    } else {
      ranges.push([start, end]);
    }
  }
  const segments = [];
  let prevEnd = 0;
  for (const [start, end] of ranges) {
    if (start > prevEnd) segments.push({ type: "collapsed", count: start - prevEnd });
    segments.push({ type: "lines", ops: ops.slice(start, end) });
    prevEnd = end;
  }
  if (prevEnd < n) segments.push({ type: "collapsed", count: n - prevEnd });
  return segments;
}

// All entries belonging to the subtree rooted at `rootEntry`, keyed by their
// path relative to that root (so siblings at corresponding positions in two
// different subtrees share the same relative key).
function subtreeEntries(flat, rootEntry) {
  const prefixLen = rootEntry.path.length;
  const map = new Map();
  for (const e of flat) {
    if (e.pathKey === rootEntry.pathKey || e.pathKey.startsWith(rootEntry.pathKey + "/")) {
      map.set(e.path.slice(prefixLen).join("/"), e);
    }
  }
  return map;
}

// Diff the manifest lists of two matched apps, matching resources by kind+name.
function diffResources(manifestsA, manifestsB) {
  const mapA = new Map((manifestsA || []).map((m) => [resourceDiffKey(m), m]));
  const mapB = new Map((manifestsB || []).map((m) => [resourceDiffKey(m), m]));
  const keys = new Set([...mapA.keys(), ...mapB.keys()]);
  const out = [];
  for (const key of keys) {
    const a = mapA.get(key), b = mapB.get(key);
    let status, yamlA = null, yamlB = null;
    if (a && b) {
      yamlA = dumpYaml(a);
      yamlB = dumpYaml(b);
      status = yamlA === yamlB ? "identical" : "changed";
    } else if (a) {
      yamlA = dumpYaml(a);
      status = "removed";
    } else {
      yamlB = dumpYaml(b);
      status = "added";
    }
    const ref = a || b;
    out.push({ key, kind: ref.kind ?? "?", name: ref.metadata?.name ?? "?", status, yamlA, yamlB });
  }
  out.sort((x, y) => x.key.localeCompare(y.key));
  return out;
}

// Diff two subtrees: match apps by relative path under each root, then diff
// each matched pair's resources.
function diffTrees(flat, entryA, entryB) {
  const mapA = subtreeEntries(flat, entryA);
  const mapB = subtreeEntries(flat, entryB);
  const relPaths = new Set([...mapA.keys(), ...mapB.keys()]);
  const apps = [];
  for (const relPath of relPaths) {
    const eA = mapA.get(relPath), eB = mapB.get(relPath);
    if (eA && eB) {
      const resources = diffResources(eA.node.manifests, eB.node.manifests);
      const changed = resources.some((r) => r.status !== "identical");
      apps.push({ relPath, status: changed ? "changed" : "identical", resources });
    } else if (eA) {
      apps.push({ relPath, status: "onlyA", resources: [] });
    } else {
      apps.push({ relPath, status: "onlyB", resources: [] });
    }
  }
  apps.sort((a, b) => a.relPath.localeCompare(b.relPath));
  return { apps };
}

function diffStatusLabel(status) {
  return {
    changed: "Changed", added: "Added", removed: "Removed",
    identical: "Identical", onlyA: "Only in A", onlyB: "Only in B",
  }[status] || status;
}

function diffMarker(type) {
  return { add: "+", del: "-", eq: " " }[type] || " ";
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
    nodePath:    { type: Array,  required: true },
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
    function childPathKey(child) {
      return [...props.nodePath, child.name].join("/");
    }
    return { showYaml, sourceOpen, sourceLabel, sourceRows, childPathKey };
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
                    class="child-tag" @click="$emit('selectApp', childPathKey(c))">{{ c.name }}</button>
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

// ── Diff components ───────────────────────────────────────────────────────

const DiffResource = {
  props: {
    resource:    { type: Object,  required: true },
    fullContext: { type: Boolean, default: false },
  },
  setup(props) {
    const open = ref(false);

    const segments = computed(() => {
      if (!open.value) return [];
      if (props.resource.status === "changed") {
        const ops = diffLines(props.resource.yamlA, props.resource.yamlB);
        return props.fullContext ? [{ type: "lines", ops }] : collapseContext(ops, 3);
      }
      if (props.resource.status === "added") {
        return [{ type: "lines", ops: props.resource.yamlB.split("\n").map((text) => ({ type: "add", text })) }];
      }
      if (props.resource.status === "removed") {
        return [{ type: "lines", ops: props.resource.yamlA.split("\n").map((text) => ({ type: "del", text })) }];
      }
      // identical
      const ops = (props.resource.yamlA ?? props.resource.yamlB).split("\n").map((text) => ({ type: "eq", text }));
      return props.fullContext ? [{ type: "lines", ops }] : collapseContext(ops, 3);
    });

    return { open, segments, diffStatusLabel, diffMarker };
  },
  template: `
    <div class="diff-resource">
      <div class="diff-resource-header" @click="open = !open">
        <span class="diff-badge" :class="resource.status">{{ diffStatusLabel(resource.status) }}</span>
        <span class="diff-resource-name">{{ resource.kind }}/{{ resource.name }}</span>
        <i class="resource-chevron" :class="{open}">›</i>
      </div>
      <div v-if="open" class="diff-lines">
        <template v-for="(seg, i) in segments" :key="i">
          <div v-if="seg.type === 'collapsed'" class="diff-collapsed">
            ⋯ {{ seg.count }} unchanged line{{ seg.count === 1 ? '' : 's' }} ⋯
          </div>
          <div v-else>
            <div v-for="(op, j) in seg.ops" :key="j" class="diff-line" :class="'diff-' + op.type">
              <span class="diff-marker">{{ diffMarker(op.type) }}</span><span class="diff-text">{{ op.text }}</span>
            </div>
          </div>
        </template>
      </div>
    </div>
  `
};

const DiffApp = {
  components: { DiffResource },
  props: {
    app:           { type: Object,  required: true },
    showIdentical: { type: Boolean, default: false },
    fullContext:   { type: Boolean, default: false },
  },
  setup(props) {
    const visibleResources = computed(() =>
      props.showIdentical ? props.app.resources : props.app.resources.filter((r) => r.status !== "identical")
    );
    const identicalCount = computed(() => props.app.resources.filter((r) => r.status === "identical").length);
    return { visibleResources, identicalCount, diffStatusLabel };
  },
  template: `
    <div class="diff-app">
      <div class="diff-app-header">
        <span class="diff-badge" :class="app.status">{{ diffStatusLabel(app.status) }}</span>
        <span class="diff-app-path">{{ app.relPath || '(root)' }}</span>
      </div>
      <template v-if="app.status === 'changed' || app.status === 'identical'">
        <diff-resource v-for="r in visibleResources" :key="r.key" :resource="r" :full-context="fullContext"></diff-resource>
        <div v-if="!showIdentical && identicalCount" class="diff-collapsed">
          {{ identicalCount }} identical resource{{ identicalCount === 1 ? '' : 's' }} hidden
        </div>
      </template>
      <div v-else class="diff-collapsed">
        {{ app.status === 'onlyA' ? 'Only present under Branch A' : 'Only present under Branch B' }}
      </div>
    </div>
  `
};

const DiffViewer = {
  components: { DiffApp },
  props: {
    result:        { type: Object,  required: true },
    labelA:        { type: String,  required: true },
    labelB:        { type: String,  required: true },
    showIdentical: { type: Boolean, default: false },
    fullContext:   { type: Boolean, default: false },
  },
  setup(props) {
    const visibleApps = computed(() =>
      props.showIdentical ? props.result.apps : props.result.apps.filter((a) => a.status !== "identical")
    );
    const identicalAppCount = computed(() => props.result.apps.filter((a) => a.status === "identical").length);
    return { visibleApps, identicalAppCount };
  },
  template: `
    <div class="diff-viewer">
      <div class="diff-header">
        <div class="diff-branch"><span class="diff-branch-label diff-branch-a">A</span> {{ labelA }}</div>
        <div class="diff-branch"><span class="diff-branch-label diff-branch-b">B</span> {{ labelB }}</div>
      </div>
      <diff-app v-for="a in visibleApps" :key="a.relPath" :app="a"
                :show-identical="showIdentical" :full-context="fullContext"></diff-app>
      <div v-if="!showIdentical && identicalAppCount" class="diff-collapsed">
        {{ identicalAppCount }} identical application{{ identicalAppCount === 1 ? '' : 's' }} hidden
      </div>
      <div v-if="!visibleApps.length && !identicalAppCount" class="diff-collapsed">
        No applications found under either branch.
      </div>
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
      <button v-if="contents.hasChart" class="btn btn-ghost browser-item"
              :class="{ 'is-selected': contents.current === seedPath }"
              @click="selectFile(contents.current)" :title="contents.current">
        ⎈ Use this chart directory
      </button>
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
  components: { FileBrowser, AppDetail, DiffViewer },

  setup() {
    // ── State
    const rootPath    = ref("");
    const recents     = ref([]);
    const isRendering = ref(false);
    const renderResult = ref(null);   // { ok, tree, error }
    const topError    = ref(null);
    const selectedApp = ref(null);
    const options     = ref({ argocdEnv: false, maxDepth: 10, valuesOverride: "" });

    // ── Navigation state (recorded in the URL query string)
    const viewState     = ref({ kind: null, open: [] }); // resource-viewer state for selectedApp
    const staleApp      = ref(null);                     // app name from URL/prior view that no longer exists
    const staleSelection = ref(null);                    // { app, kind, open } to retry on the next render
    const pendingSelection = ref(null);                  // selection to apply once the next render completes

    // ── Diff mode
    const diffMode          = ref(false);
    const diffA             = ref(null);  // pathKey of "Branch A"
    const diffB             = ref(null);  // pathKey of "Branch B"
    const diffShowIdentical = ref(false);
    const diffFullContext   = ref(false);
    const pendingDiff       = ref(null);  // { a, b } pathKeys to apply once the next render completes

    // Sidebar section open/closed
    const sections = ref({ recents: true, browser: false, options: false, display: false, diff: false });

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

    // selectedApp holds a pathKey (names from root to node joined by "/"),
    // which uniquely identifies a node even if its name is shared with
    // leaves in other branches of the tree.
    const selectedEntry = computed(() => {
      if (!flat.value.length) return null;
      if (selectedApp.value) {
        const found = flat.value.find((e) => e.pathKey === selectedApp.value);
        if (found) return found;
      }
      return flat.value[0] ?? null;
    });
    const selectedNode = computed(() => selectedEntry.value?.node ?? null);

    // ── Diff mode derived state
    const diffOptions = computed(() =>
      flat.value.map((e) => ({ pathKey: e.pathKey, label: e.path.join(" › ") }))
    );
    const diffResult = computed(() => {
      if (!diffMode.value || !diffA.value || !diffB.value) return null;
      const entryA = flat.value.find((e) => e.pathKey === diffA.value);
      const entryB = flat.value.find((e) => e.pathKey === diffB.value);
      if (!entryA || !entryB) return null;
      return diffTrees(flat.value, entryA, entryB);
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
        diff: diffMode.value ? "1" : null,
        diffA: diffMode.value ? diffA.value : null,
        diffB: diffMode.value ? diffB.value : null,
      });
    }

    watch([diffMode, diffA, diffB], syncUrl);

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
          values_override: options.value.valuesOverride.trim() || null,
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
          if (pendingDiff.value) {
            const { a, b } = pendingDiff.value;
            pendingDiff.value = null;
            if (a && findNode(flatList, a)) diffA.value = a;
            if (b && findNode(flatList, b)) diffB.value = b;
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
        if (params.get("diff") === "1") {
          diffMode.value = true;
          pendingDiff.value = { a: params.get("diffA") || null, b: params.get("diffB") || null };
        }
        doRender();
      }
    });

    return {
      rootPath, recents, isRendering, renderResult, topError,
      selectedApp, options, sections,
      flat, prefixes, selectedNode, selectedEntry,
      totalApps, totalResources, totalErrors,
      loadRecents, removeRecent, selectPath, onBrowserSelect, doRender,
      basename, dirname,
      sidebarWidth, onHandleMouseDown,
      displayMode, expandSeq, collapseSeq, expandAll, collapseAll,
      viewState, staleApp, selectApp, onResourceStateChange, clearNavigation,
      diffMode, diffA, diffB, diffShowIdentical, diffFullContext, diffOptions, diffResult,
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
          <label class="path-label">Root Application manifest or chart directory</label>
          <input class="path-input" v-model="rootPath"
                 placeholder="/path/to/root-app.yaml or chart dir"
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
            <div class="option-row option-col">
              <label for="opt-values">Values override (chart-directory roots)</label>
              <textarea id="opt-values" class="values-textarea" v-model="options.valuesOverride"
                        rows="5" placeholder="key: value"></textarea>
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

        <!-- Diff mode (shown after a render) -->
        <div class="sidebar-section" v-if="renderResult">
          <button class="section-header" @click="sections.diff = !sections.diff">
            Diff
            <i class="section-chevron" :class="{open: sections.diff}">›</i>
          </button>
          <div v-if="sections.diff" class="section-body">
            <div class="option-row">
              <input type="checkbox" v-model="diffMode" id="opt-diff-mode">
              <label for="opt-diff-mode">Compare two branches</label>
            </div>
            <template v-if="diffMode">
              <div class="option-row option-col">
                <label for="opt-diff-a">Branch A</label>
                <select id="opt-diff-a" class="diff-select" v-model="diffA">
                  <option :value="null" disabled>Select an application…</option>
                  <option v-for="o in diffOptions" :key="o.pathKey" :value="o.pathKey">{{ o.label }}</option>
                </select>
              </div>
              <div class="option-row option-col">
                <label for="opt-diff-b">Branch B</label>
                <select id="opt-diff-b" class="diff-select" v-model="diffB">
                  <option :value="null" disabled>Select an application…</option>
                  <option v-for="o in diffOptions" :key="o.pathKey" :value="o.pathKey">{{ o.label }}</option>
                </select>
              </div>
              <div class="option-row">
                <input type="checkbox" v-model="diffShowIdentical" id="opt-diff-identical">
                <label for="opt-diff-identical">Show identical apps/resources</label>
              </div>
              <div class="option-row">
                <label>Diff style</label>
                <div class="view-toggle">
                  <button class="btn btn-sm" :class="{'btn-primary': !diffFullContext}"
                          @click="diffFullContext = false">Minimal</button>
                  <button class="btn btn-sm" :class="{'btn-primary': diffFullContext}"
                          @click="diffFullContext = true">Full context</button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- App tree -->
        <div v-if="flat.length" class="tree-section">
          <div class="tree-label">Applications</div>
          <button v-for="({depth, node, pathKey}, i) in flat" :key="pathKey"
                  class="tree-item"
                  :class="{active: pathKey === (selectedEntry && selectedEntry.pathKey)}"
                  @click="selectApp(pathKey)">
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
          <p>Select a root <code>kind:Application</code> manifest, or a directory containing a Helm chart, using Browse… or by typing the path, then click <strong>Render</strong>.</p>
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
          <template v-if="diffMode">
            <div class="divider"></div>
            <div v-if="!diffA || !diffB" class="diff-collapsed">
              Select two applications in the Diff section of the sidebar to compare.
            </div>
            <diff-viewer v-else-if="diffResult" :result="diffResult"
                         :label-a="diffA.split('/').join(' › ')"
                         :label-b="diffB.split('/').join(' › ')"
                         :show-identical="diffShowIdentical"
                         :full-context="diffFullContext"></diff-viewer>
          </template>
          <template v-else>
            <div v-if="staleApp" class="stale-warning">
              <span>⚠ Application "{{ staleApp.split('/').join(' › ') }}" is no longer present in the rendered tree.</span>
              <button class="btn btn-sm" @click="clearNavigation">Clear navigation</button>
            </div>
            <div class="divider"></div>
            <app-detail v-if="selectedNode" :node="selectedNode" :node-path="selectedEntry.path" :key="selectedEntry.pathKey"
                        :display-mode="displayMode"
                        :expand-seq="expandSeq"
                        :collapse-seq="collapseSeq"
                        :initial-active-kind="viewState.kind"
                        :initial-open-keys="viewState.open"
                        @select-app="selectApp"
                        @state-change="onResourceStateChange"></app-detail>
          </template>
        </template>

      </main>
    </div>
  `
}).mount("#app");
