# Developer notes

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
