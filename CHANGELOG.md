# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- New feature that makes it easy to generate per-environment root application instances - for easy validation and diffing.

### Changed
- Diff mode and the environment map now show an on/off checkbox in their sidebar section heading. You no longer need to open the section to turn either one on or off.
- Diff mode now shows a banner in the main view when it's active, with a button there to turn it off.
- Turning on diff mode opens its sidebar section automatically, unless you already picked both branches.
- Removed the environment map's "None" radio option. The header checkbox already turns it on and off.
- Renamed the diff status "Changed" to "Differs". Diff mode compares two branches of the same render, not a change from a prior state.
- The Applications/Resources/Errors counts in the main view now show the selected app's own subtree next to the grand total (e.g. "3 / 6 Applications"), outside diff mode.

### Fixed
- Diff mode ignored each app's own Application resource, and compared only the resources it renders. Two apps with an identical rendered ConfigMap, say, but a different `targetRevision` or Helm value in their own Application spec, showed as "Identical". Diff mode now also compares each app's Application resource, so a change there always shows up.

## [0.1.0] - 2026-08-31

Initial release. See the [README](README.md) for full feature documentation.

[Unreleased]: https://github.com/Morlock-Collective/argocheck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Morlock-Collective/argocheck/releases/tag/v0.1.0
