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

### Fixed
- The Diff and Environment map section headings had a different text style (case, letter spacing) from the other sidebar sections. They now match.

## [0.1.0] - 2026-08-31

Initial release. See the [README](README.md) for full feature documentation.

[Unreleased]: https://github.com/Morlock-Collective/argocheck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Morlock-Collective/argocheck/releases/tag/v0.1.0
