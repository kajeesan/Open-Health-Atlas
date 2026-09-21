# OpenHealthAtlas working instructions

## Before editing

- Use fictional data and preserve public attribution and licenses. (CONTRIBUTING.md §Principles; §Contributing)
- Keep canonical records, deterministic calculations and validated writes under OpenHealthAtlas authority. (docs/ARCHITECTURE.md §Deterministic, interpretive, and agent boundaries)
- Preserve wired workflows, CLI compatibility and unrelated changes; use the agreed branch and review checkpoints. (docs/DEVELOPMENT.md §Working conventions)
- Preserve time, unit, provenance and identity contracts. (docs/SYSTEM_DESIGN.md §Time and identity)
- Choose checks for retained behavior and report exact evidence. (docs/TESTING.md §Auditable partitions; docs/DEVELOPMENT.md §Review evidence)

## Navigation

- Read `docs/DEVELOPMENT.md` §Working conventions for privacy, commit and publication boundaries.
- Read `docs/REFACTORING.md` for the current checkpoint; verify consequential claims in current source.
- Read `docs/ARCHITECTURE.md` §Major packages to locate owning modules.
- Read `docs/DEVELOPMENT.md` §Documentation ownership and §Writing tools before using project extensions.
