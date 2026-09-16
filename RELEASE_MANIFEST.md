# Public release manifest

`RELEASE_MANIFEST.tsv` is an integrity inventory of the files in this public
release. It records each current public path, its SHA-256 hash, byte size, and
broad file kind.

## Verify

From the repository root:

```bash
python3 scripts/release_manifest.py verify
```

Verification fails if a tracked path is missing or added without regenerating
the manifest, if a listed file is not a regular file, or if any hash or byte
count differs.

## Regenerate

After intentionally changing the public tree, stage the intended file set and
run:

```bash
python3 scripts/release_manifest.py build
python3 scripts/release_manifest.py verify
```

The manifest deliberately excludes itself so regeneration has no recursive
hash dependency. Third-party license and version information remains in
`THIRD_PARTY_NOTICES.md` and `app/static/vendor/VENDOR.md`.
