# Developer notes

## Dependency pinning

`pyproject.toml`'s `dependencies` are pinned to exact (`==`) versions, not
`>=` ranges. This is deliberate: a plain `pip install argocheck` from PyPI
only ever reads *this* package's own metadata, and pip's resolver then picks
whatever version of each *direct* dependency satisfies it — with `>=`, that
could be a version published minutes ago, which corporate/constrained mirrors
(Artifactory, Nexus, air-gapped environments) often quarantine for a window
after publish, breaking installs entirely until the quarantine clears. Exact
pins mean `pip install argocheck` always resolves to the same, already-vetted
direct dependency versions.

Note the limit of this: pip metadata has no mechanism to pin *transitive*
dependencies (e.g. fastapi's own dependency on starlette) — pip resolves
those from each direct dependency's own (unpinned) declared ranges,
regardless of anything in argocheck's metadata. There's deliberately no lock
file pinning the full transitive graph: nothing would actually consume it
(CI tests the same way a real `pip install argocheck` resolves, so it can
catch upstream breakage — see below), so it would just be unmaintained
overhead. Local dev setup and CI both use the same plain
`pip install -e ".[dev]"`.

### Automated updates (`.github/workflows/update-deps.yml`)

Runs weekly (Mondays, plus manual `workflow_dispatch`): `scripts/bump_dependencies.py`
checks each exact-pinned dependency in `pyproject.toml` against PyPI's latest
release and rewrites the pins that are behind, then `pip install -e ".[dev]"`
and the full test suite run against the result — the same install method CI
uses, so a passing bump here means CI would pass too. **A PR is only opened
if the tests pass** — a failure just ends the run with nothing proposed, so
a broken bump never reaches even a PR. Passing tests aren't proof against
everything (e.g. a compromised release), so review the diff before merging
rather than rubber-stamping it.

## Releasing (`.github/workflows/publish.yml`)

Pushing a tag matching `v[0-9]+.[0-9]+.[0-9]+` (e.g. `v0.2.0`) builds the
package, verifies the tag matches `pyproject.toml`'s `version` and that
`CHANGELOG.md` has a matching `## [X.Y.Z]` section, then publishes to PyPI and
creates a GitHub release with that changelog section as the release notes.

Release checklist:

1. Bump `version` in `pyproject.toml`.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`.
3. Commit, then tag that commit `vX.Y.Z` and push the tag.

### One-time setup (credentials)

The pipeline uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) — **no API tokens or secrets are stored in this repo.** Two things
need to be configured once, by someone with admin access to both sides:

1. **GitHub**: create an environment named `pypi` (repo Settings →
   Environments). Adding required reviewers here turns every publish into a
   manual-approval gate — recommended once this has real users.
2. **PyPI**: on the `argocheck` project page (or via "Add a new pending
   publisher" before the project exists), register a trusted publisher with:
   - Owner: `Morlock-Collective`
   - Repository: `argocheck`
   - Workflow filename: `publish.yml`
   - Environment name: `pypi`

Once both are set up, no further credential maintenance is needed — GitHub
mints a short-lived OIDC token per run that PyPI verifies against this config.
