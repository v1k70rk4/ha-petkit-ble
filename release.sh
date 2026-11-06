#!/bin/bash

# Release script for PetKit BLE Integration.
#
# main only takes changes through pull requests, so a release has two steps:
#
#   ./release.sh [patch|minor|major|X.Y.Z]   open a release pull request that
#                                            bumps manifest.json
#   ./release.sh --tag                       after that pull request is merged,
#                                            tag main; the Release workflow
#                                            creates the GitHub release
#
# Needs git and the GitHub CLI (gh), signed in.

set -eE  # -E: the ERR trap also fires inside functions

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

MANIFEST="custom_components/petkit_ble/manifest.json"
DRY_RUN=false
AUTO_YES=false
TAG_MODE=false
VERSION_TYPE="patch"

show_error() {
    echo -e "${RED}❌ Error: $1${NC}" >&2
    if [ -n "$2" ]; then
        echo -e "${YELLOW}💡 $2${NC}" >&2
    fi
}
show_info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
show_success() { echo -e "${GREEN}✅ $1${NC}"; }

show_usage() {
    cat <<EOF
PetKit BLE release script

Usage:
  $0 [OPTIONS] [patch|minor|major|X.Y.Z]   Open a release pull request (default: patch)
  $0 [OPTIONS] --tag                       Tag main after the release pull request is merged

Options:
  --dry-run, -n   Show what would be done without changing anything
  --yes, -y       Do not ask for confirmation
  --help, -h      Show this help

A version with a suffix (1.3.0-beta.1) becomes a pre-release.
EOF
}

run() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY RUN] $*"
    else
        "$@"
    fi
}

confirm() {
    if [ "$AUTO_YES" = true ] || [ "$DRY_RUN" = true ]; then
        return 0
    fi
    read -r -p "$1 [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=true ;;
        --yes|-y) AUTO_YES=true ;;
        --tag) TAG_MODE=true ;;
        --help|-h) show_usage; exit 0 ;;
        -*) show_error "Unknown flag: $1" "Use --help to see the options"; exit 1 ;;
        *) VERSION_TYPE="$1" ;;
    esac
    shift
done

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    show_error "Not in a git repository"
    exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
    show_error "The GitHub CLI (gh) is not installed" "https://cli.github.com/"
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    show_error "The working tree has uncommitted changes" "Commit or stash them first; a release only changes manifest.json"
    exit 1
fi

get_manifest_version() {
    python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST"
}

show_info "Updating main..."
git fetch -q origin main
git checkout -q main
git merge -q --ff-only origin/main

CURRENT_VERSION=$(get_manifest_version)

if [ "$TAG_MODE" = true ]; then
    TAG="v$CURRENT_VERSION"
    if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
        show_error "$TAG already exists on GitHub" "Open a release pull request for the next version first: $0 patch"
        exit 1
    fi
    show_info "main is at $(git rev-parse --short HEAD), manifest.json says $CURRENT_VERSION"
    if ! confirm "Tag main as $TAG and push the tag?"; then
        echo "Cancelled."
        exit 0
    fi
    run git tag -a "$TAG" -m "Release $TAG"
    run git push origin "$TAG"
    show_success "$TAG pushed. The Release workflow creates the GitHub release in a minute or two."
    exit 0
fi

case "$VERSION_TYPE" in
    major|minor|patch)
        IFS='.' read -r major minor patch <<<"${CURRENT_VERSION%%-*}"
        case "$VERSION_TYPE" in
            major) major=$((major + 1)); minor=0; patch=0 ;;
            minor) minor=$((minor + 1)); patch=0 ;;
            patch) patch=$((patch + 1)) ;;
        esac
        NEW_VERSION="$major.$minor.$patch"
        ;;
    *)
        NEW_VERSION="${VERSION_TYPE#v}"
        if [[ ! "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
            show_error "Invalid version: $VERSION_TYPE" "Use patch, minor, major or a version like 1.2.3"
            exit 1
        fi
        ;;
esac

BRANCH="release/v$NEW_VERSION"
if git ls-remote --exit-code --tags origin "refs/tags/v$NEW_VERSION" >/dev/null 2>&1; then
    show_error "v$NEW_VERSION is already released"
    exit 1
fi
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    show_error "A local $BRANCH branch is left over from an earlier attempt" \
        "Delete it if it is not needed: git branch -D $BRANCH"
    exit 1
fi
# Exit code 2 means the branch is absent; anything else non-zero means the
# lookup itself failed, and we must not push over a branch we could not see.
lookup_status=0
git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1 || lookup_status=$?
if [ "$lookup_status" -eq 0 ]; then
    show_error "$BRANCH already exists on GitHub" \
        "Finish its pull request (gh pr create --base main --head $BRANCH), or delete it: git push origin --delete $BRANCH"
    exit 1
elif [ "$lookup_status" -ne 2 ]; then
    show_error "Could not check GitHub for $BRANCH" "Check network access and gh/git authentication, then retry"
    exit 1
fi

echo "  Current version: $CURRENT_VERSION"
echo "  New version:     $NEW_VERSION"
echo "  Branch:          $BRANCH"
if ! confirm "Open the release pull request?"; then
    echo "Cancelled."
    exit 0
fi

BRANCH_CREATED=false
PUSH_STARTED=false
PUSHED=false
# If anything below fails, go back to a clean main instead of leaving the
# operator on a half-made release branch.
cleanup_on_error() {
    status=$?
    trap - ERR
    set +e
    show_error "Release pull request was not opened"
    git checkout -q -- "$MANIFEST" 2>/dev/null
    git checkout -q main
    remote_status=2
    if [ "$PUSHED" = true ]; then
        remote_status=0
    elif [ "$PUSH_STARTED" = true ]; then
        # A failed push may still have updated the remote; ask it.
        git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1
        remote_status=$?
    fi
    if [ "$remote_status" -eq 0 ]; then
        echo -e "${YELLOW}💡 $BRANCH is already on GitHub. Open its pull request by hand:" >&2
        echo -e "   gh pr create --base main --head $BRANCH${NC}" >&2
    elif [ "$BRANCH_CREATED" != true ]; then
        :  # failed before this run created $BRANCH; leave any such branch alone
    elif [ "$remote_status" -eq 2 ]; then
        if git branch -q -D "$BRANCH" 2>/dev/null; then
            echo -e "${YELLOW}💡 Nothing was pushed; the local $BRANCH branch was removed.${NC}" >&2
        else
            echo -e "${YELLOW}💡 Nothing was pushed, but the local $BRANCH branch could not be removed." >&2
            echo -e "   Delete it by hand: git branch -D $BRANCH${NC}" >&2
        fi
    else
        echo -e "${YELLOW}💡 Could not tell whether $BRANCH reached GitHub; the local branch was kept." >&2
        echo -e "   Check with: git ls-remote --heads origin $BRANCH${NC}" >&2
    fi
    exit "$status"
}
if [ "$DRY_RUN" = false ]; then
    trap cleanup_on_error ERR
fi

run git checkout -q -b "$BRANCH"
BRANCH_CREATED=true
run env PYTHONIOENCODING=utf-8 python3 .github/update-manifest-version.py "$NEW_VERSION"
if [ "$DRY_RUN" = false ]; then
    OTHER_CHANGES=$(git status --porcelain -- . ":!$MANIFEST")
    if [ -n "$OTHER_CHANGES" ]; then
        echo "$OTHER_CHANGES" >&2
        show_error "Files other than manifest.json changed during the version bump"
        false
    fi
fi
run git add "$MANIFEST"
run git commit -q -m "chore: release v$NEW_VERSION"
PUSH_STARTED=true
run git push -q -u origin "$BRANCH"
PUSHED=true
run gh pr create --base main --head "$BRANCH" \
    --title "Release v$NEW_VERSION" \
    --body "Bumps \`manifest.json\` to $NEW_VERSION. After it is merged, run \`./release.sh --tag\` to tag main; the Release workflow then creates the GitHub release."
trap - ERR
run git checkout -q main

show_success "Release pull request opened. Once it is merged: $0 --tag"
