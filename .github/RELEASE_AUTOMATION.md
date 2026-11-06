# Release process

Releases are driven by version tags (`vX.Y.Z`, or `vX.Y.Z-suffix` for a pre-release).

## Steps

`main` only takes changes through pull requests, the release included.

1. Run `./release.sh [patch|minor|major|X.Y.Z]` (add `--dry-run` to preview).
   It creates a `release/vX.Y.Z` branch from an up-to-date `main`, sets
   `version` in `custom_components/petkit_ble/manifest.json`, commits only that
   file and opens a "Release vX.Y.Z" pull request. A dirty working tree stops it.
2. Merge the pull request once CI is green.
3. Run `./release.sh --tag`. It updates `main`, tags it with the version that is
   now in `manifest.json` and pushes the tag, which starts the release workflow.
4. Optionally replace the generated title and notes once the workflow has
   created the release (`gh release edit vX.Y.Z --title "..." --notes-file notes.md`).
   The workflow never touches a release that already exists, so edits are kept.

## `.github/workflows/release.yml`

On a tag push it:

- rejects any tag that is not a plain version (the tag name is never
  interpolated into a script);
- fails if `manifest.json` does not already carry the tag's version (it never
  pushes to `main` itself);
- creates the GitHub release with generated notes, unless one already exists.
  Tags with a `-suffix` become pre-releases and are not marked as latest.

`.github/update-manifest-version.py X.Y.Z` can also be run by hand to set the
manifest version.
