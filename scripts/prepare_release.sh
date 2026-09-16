#!/usr/bin/env bash
# Prepares an argocheck release:
#   1. Turns CHANGELOG.md's "## [Unreleased]" section into a dated release
#      section for the version currently in pyproject.toml.
#   2. Commits that as "v<version>" and creates an annotated tag v<version>
#      on that commit.
#   3. Preps the next version: bumps pyproject.toml's minor version (patch
#      reset to 0) and adds a fresh, empty "## [Unreleased]" section. Left
#      uncommitted, for review before committing.
#
# Run from anywhere inside the repo. Does not push anything.
set -euo pipefail

cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

PYPROJECT="pyproject.toml"
CHANGELOG="CHANGELOG.md"

if [[ ! -f "$PYPROJECT" || ! -f "$CHANGELOG" ]]; then
  echo "error: expected $PYPROJECT and $CHANGELOG at the repo root" >&2
  exit 1
fi

# ── Preconditions ────────────────────────────────────────────────────────

if ! git diff --quiet -- || ! git diff --cached --quiet --; then
  echo "error: uncommitted changes to tracked files. Commit or stash them first:" >&2
  git status --short >&2
  exit 1
fi

VERSION=$(grep -m1 -E '^version = "' "$PYPROJECT" | sed -E 's/version = "(.*)"/\1/')
if [[ -z "$VERSION" ]]; then
  echo "error: could not find a version in $PYPROJECT" >&2
  exit 1
fi
if ! [[ "$VERSION" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "error: version '$VERSION' in $PYPROJECT is not MAJOR.MINOR.PATCH" >&2
  exit 1
fi
MAJOR="${BASH_REMATCH[1]}"
MINOR="${BASH_REMATCH[2]}"

TAG="v$VERSION"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 1
fi
if grep -qF "## [$VERSION]" "$CHANGELOG"; then
  echo "error: $CHANGELOG already has a [$VERSION] section" >&2
  exit 1
fi
if ! grep -qF "## [Unreleased]" "$CHANGELOG"; then
  echo "error: $CHANGELOG has no ## [Unreleased] section" >&2
  exit 1
fi

# The Unreleased section must have at least one non-blank line before the
# next "## " heading — otherwise there's nothing to release.
unreleased_body=$(awk '
  /^## \[Unreleased\]/ { flag=1; next }
  /^## / { flag=0 }
  flag
' "$CHANGELOG")
if ! grep -q '[^[:space:]]' <<<"$unreleased_body"; then
  echo "error: the Unreleased section in $CHANGELOG is empty — nothing to release" >&2
  exit 1
fi

# ── Release the current version ─────────────────────────────────────────

DATE=$(date +%Y-%m-%d)

sed -i "s/^## \[Unreleased\]\$/## [$VERSION] - $DATE/" "$CHANGELOG"

git add "$CHANGELOG"
git commit -m "$TAG"
git tag -a "$TAG" -m "$TAG"

echo "Released $TAG (commit $(git rev-parse --short HEAD))."

# ── Prep the next version ───────────────────────────────────────────────

NEXT_VERSION="$MAJOR.$((MINOR + 1)).0"
sed -i "s/^version = \"$VERSION\"\$/version = \"$NEXT_VERSION\"/" "$PYPROJECT"

# Insert a fresh "## [Unreleased]" section right before the one we just
# dated, rather than a single-line sed replacement, since inserting two
# lines (heading + blank separator) portably in one sed call is awkward.
awk -v ver="$VERSION" -v date="$DATE" -v inserted=0 '
  $0 == "## [" ver "] - " date && inserted == 0 {
    print "## [Unreleased]"
    print ""
    inserted = 1
  }
  { print }
' "$CHANGELOG" > "$CHANGELOG.tmp"
mv "$CHANGELOG.tmp" "$CHANGELOG"

echo "Bumped $PYPROJECT to $NEXT_VERSION and added a new Unreleased section in $CHANGELOG."
echo "Review the changes, then commit them yourself, e.g.:"
echo "  git commit -am \"Bump version for next release\""
echo
echo "Not pushed. When ready:"
echo "  git push && git push origin $TAG"
