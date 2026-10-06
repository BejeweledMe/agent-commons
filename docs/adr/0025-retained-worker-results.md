# ADR 0025: Retained worker results and exact artifact review

Date: 2026-10-01.
Status: Implemented for the authorized G5/G7 increment; product acceptance is separate.

Worker image metadata alone cannot preserve earlier pixels when a source path is
overwritten. A reported development URL also cannot establish durable build bytes,
server ownership or reachability. The implementation retains image and bounded
static build content in a local content-addressed store, referenced by an existing
artifact manifest. The ledger still contains metadata only. Each worker publication
creates a separate artifact version; task, delegation, producer, content digest and
artifact event revision remain exact canonical bindings.

The detailed storage, bounds, retry and reviewer read contract belongs to
[Protocol](../PROTOCOL.md#retained-worker-results). This is deliberately a local
storage boundary: backup/restore must preserve content with workspace operational
state. Missing bytes remain unavailable. Earlier metadata-only publications remain
readable through their original source contract and are not retroactively copied.

Static builds are deterministic ZIP downloads with file hashes. They are never
served as executable HTML on the Commons authenticated origin. Opening a local
server is an explicit operator action on a separate origin. An owned, hosted static
preview service, sandbox or arbitrary application process manager is a separate
feature; this increment does not claim it exists.

Existing artifact-target reviews already bind an exact event revision. Reusing
that contract avoids a second approval lifecycle. Independent reviewers receive
scope-frozen image pixels or bounded text entries from the retained build. A
manifest-only build read cannot satisfy review finalization. Verification of
hashes/read coverage proves evidence inspection, not runtime behavior, visual
quality or human acceptance. Current task review and exact result review have
separate DTO fields and labels. A delegated approval remains undisplayed until its
matching delegation succeeds.

Rollback preserves legacy manifest reads; retained metadata requires retained bytes
and cannot silently fall back to the overwritten source. No automatic quota eviction
or canonical history rewrite is permitted.
