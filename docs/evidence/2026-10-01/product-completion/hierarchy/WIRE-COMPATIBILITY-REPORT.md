# G6 incremental legacy-wire compatibility repair

Apply `g6-wire-compatibility.patch` **after** the last delivered final G6 patches.
This is a four-file incremental patch, not a regenerated full G6 patch.
`wire-compatibility-manifest.json` lists exact before/after SHA-256 for each file;
every base hash matches the previously delivered final integration manifest
`cabfad11aa330749267d43d3280985109f63a8595bb4b6058c8436392f0f11a8`.
The patch was applied to captured before-images and all four result hashes match.
`before-wire-fix/` preserves the relevant source bytes and prior delivery artifacts.

The regression came from adding task_kind=task and parent_task_id=null to every
raw TaskRecord during projection. Those extra keys changed legacy snapshot hashes.
The reducer now retains the fields actually present in canonical events. Existing
consumer and browser DTO reads already supply task/unparented defaults using get.
Explicit hierarchy fields, including an explicit null detach, remain serialized.
No change to semantics v7, hierarchy editing/CAS, corrections, retries, manager.py
or frontend source is needed. ADR 0024 now states this serialization boundary.

Verification: **67 Python tests passed**, including both previously failing frozen
ledger fixtures, task projection/explicit field preservation, hierarchy corrections
and v7 floors, same/separate-manager cycle races, CAS/retry editing, UI routes and
tracker DTOs. See `wire-fix-python.log`. Ruff check and formatting of the three
changed Python files pass.

Both frozen fixture files are byte-identical to the original baseline. Their
snapshot SHA is restored to the existing expected value:
`73245415a8c1c5f17227fc0e4263467b02edec3c0e56e79f655ec1cfd95b66df`.
No frozen hash was edited. The task-projection test's original expected mapping
was restored, and new assertions distinguish absent legacy fields from explicit
new hierarchy fields.

No main checkout, canonical ledger, installed tool or shipped asset was changed.
The original final patches/manifest remain unchanged; use this incremental patch
for the already-integrated tree. Full combined checks remain with root.
