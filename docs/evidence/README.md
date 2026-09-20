# Evidence retention policy

Decision of record: `repo/evidence-retention` (owner, 2026-09-18). This
directory holds revision-bound proof for reviews and acceptances. It is written
once per boundary and never edited; it is not documentation and nothing in the
code or tests reads it.

## What goes in

- One integration manifest per wave (`<date>/wave-N-integration-manifest.json`):
  the files, hashes, test counts and asset names of the tree that passed
  `make check` before the wave was merged.
- The latest source manifest of each work package (`wp-XX-source-manifest.json`
  or its final `-vN`). When a review rejects and a new version is registered,
  the superseded version is removed from the tree in the same commit; its bytes
  remain reachable through the artifact hash in the ledger and through the tag
  `archive/2026-09-pre-consolidation` for everything registered before the
  consolidation.
- Screenshots only when they prove behaviour a manifest cannot: an EN/RU pair
  or a narrow layout for a UI package, with commit, asset name, viewport and the
  steps taken before the frame recorded in a sibling `README.md`.
- Bounded result summaries from live provider checks, without prompts,
  transcripts, credentials, home paths or user data.

## What stays out

Full logs, complete source inventories, repeated screenshots of unchanged
screens, and any counts that would otherwise be quoted as documentation. Test
totals belong here and in the ledger, never in guides or contracts.

## Closing a dated directory

A `<date>/` directory is closed when its wave is merged. After that it only
receives the deletion of superseded manifest versions described above. Canonical
ledger history is never rewritten; removing a superseded file from the tree does
not invalidate the artifact record that hashed it.
