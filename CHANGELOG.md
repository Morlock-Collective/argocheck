# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Added a new logo instead of the too-helm-adjacent ships wheel (a.k.a helm) with a divers helmet, because I like divers helmets (to look at, not to wear).
- The web interface now shows the running argocheck version next to the sidebar title.
- New "Ignore targetRevision" option (CLI: `--ignore-target-revision`). Resolves every source as if targetRevision were unset. A local git repo uses its working tree as-is, a remote git repo its default branch, a Helm repo chart its latest version. Useful against a local checkout that would normally pin a specific revision. Does not affect rendering of the application manifests themselves.
- The environment map's inline YAML input now offers "Save to file…" once it renders a map. It writes the YAML to a file and switches the section to "File path", pointing at that file, keeping the current leaf selection.
- The web interface's Options and Environment map settings now reflect in the page's URL, alongside the root path/selected app/diff state it already tracked. A values override and an inline environment map YAML are base64-encoded. Bookmarking or sharing the URL restores all of it.

### Changed
- Moved the Display controls (Tabs/List, Expand all, Collapse all) out of their own sidebar section, next to the Applications/Resources/Errors counts in the main view. Labeled and boxed as one group, so it doesn't read as four unrelated buttons. Tabs/List is now a single joined toggle, not two separate buttons — also applied to the Diff style Minimal/Full context toggle, for consistency.
- **Breaking:** reworked the environment map spec. `argocheck_root` is now optional (default: `environments`). `argocheck_variable_mappings` is now a mapping of level (1..leaf_depth) to variable name, not a list — a level with no entry just nests the tree. A leaf's own key/value pairs are now optional. See the Environment maps guide topic and README for the current spec.

### Fixed
- `argocheck --version` reported 0.1.0 after the version bumps in pyproject.toml, because the editable install's own metadata never refreshed. A `pip install -e .` now keeps it in sync.
- The web interface's "Ignore targetRevision" option had no effect once rendering an environment map's selected leaves — only the leaf-enumeration step honored it.

## [0.2.0] - 2026-09-05

### Added
- New feature that makes it easy to generate per-environment root application instances - for easy validation and diffing.
- A built-in guide covering the basics, options, environment maps, and diff mode. On the CLI, read it with `argocheck --guide` (all topics) or `argocheck --guide-topic TOPIC` (one topic). In the web interface, click a "?" button next to the sidebar logo, Options, Environment map, or Diff section.
- Diff mode now warns if Branch A and Branch B are the same application, or sit at different depths in the tree. It still runs the comparison either way.

### Changed
- Diff mode and the environment map now show an on/off checkbox in their sidebar section heading. You no longer need to open the section to turn either one on or off.
- Diff mode now shows a banner in the main view when it's active, with a button there to turn it off.
- Turning on diff mode opens its sidebar section automatically, unless you already picked both branches.
- Removed the environment map's "None" radio option. The header checkbox already turns it on and off.
- Renamed the diff status "Changed" to "Differs". Diff mode compares two branches of the same render, not a change from a prior state.
- The Applications/Resources/Errors counts in the main view now show the selected app's own subtree next to the grand total (e.g. "3 / 6 Applications"), outside diff mode.
- The Applications/Resources/Errors counts now show a tooltip on hover explaining the two numbers.
- The manifest file/chart directory field now shows its full path as a tooltip on hover.
- Renamed the environment map's "Paste YAML" option to "YAML". You can also write it there directly, not just paste it.
- The environment map's sidebar section now opens automatically when you turn it on with no file path or YAML given yet. This matches Diff mode's behavior.
- Added a favicon: a ship's wheel, matching the sidebar logo. It's black in light mode and white in dark mode.

### Fixed
- Diff mode ignored each app's own Application resource, and compared only the resources it renders. Two apps with an identical rendered ConfigMap, say, but a different `targetRevision` or Helm value in their own Application spec, showed as "Identical". Diff mode now also compares each app's Application resource, so a change there always shows up.
- On a short browser window, with several sidebar sections expanded, the application tree could become unreachable, even by scrolling.

## [0.1.0] - 2026-08-31

Initial release. See the [README](README.md) for full feature documentation.

---

[Unreleased]: https://github.com/Morlock-Collective/argocheck/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Morlock-Collective/argocheck/releases/tag/v0.2.0
[0.1.0]: https://github.com/Morlock-Collective/argocheck/releases/tag/v0.1.0
