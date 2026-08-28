# Review and provenance register

This register classifies the architecture-review working set. It is an
evidence index, not a decision and not a claim about which model or session
authored a document. A review is not project truth merely because it is present
in the repository; implementation requires an owner-authorized decision,
exact-revision evidence, and the normal acceptance contract.

## Architecture-review working set

Snapshot taken on 2026-08-28 in branch `codex/context-gallery-program`, current
checkout `e071533c2643ca0f091c4f9dd8f1531740260cb3`.

| File | Classification | SHA-256 | Boundary / note |
| --- | --- | --- | --- |
| [`agent_commons_product_architecture_review.md`](../../agent_commons_product_architecture_review.md) | User-provided working input; tracked historical evidence | `7aff0941e61e88e6ee320ae2903f6cbb0c19bcef6507eb0c24a762936c3ea968` | Unique 24-Aug review, based on `f998e33`; not canonical. |
| [`claude_architecture_improvement_review.md`](../../claude_architecture_improvement_review.md) | Provenance/workspace evidence; tracked historical evidence | `61e49c44b1b69b4294a93b72912030f050118067d3c12d3255d7ae3278c0c788` | 25-Aug review material; hash records bytes, not authorship. |
| [`codex_architecture_improvement_review.md`](../../codex_architecture_improvement_review.md) | Rewritten review proposal; tracked historical evidence | `2d84d3cf10571dd082a18c38d2e90190695317bcb74d7c131ebf2054c9f46aa8` | Byte-distinct current Codex working copy; not canonical. |

## Missing local provenance copy

`codex_architecture_improvement_review.md` contains a statement that a prior
version was saved as `codex_architecture_improvement_review.provenance-copy.md`.
That path is **absent** from the current checkout, index, and ordinary branch
history. A captured older blob is referenced by the desktop turn-diff capture
under an external `refs/codex/turn-diffs/checkpoints/...` ref; its recorded
SHA-256 is:

```text
558dc6fbdca6ed1a4a33bf7df29e75f8e49e088c32fa5e084c04d6210147be72
```

This register deliberately does not materialize that blob as a fourth review
file or infer authorship from its existence. The absence is a provenance fact
to resolve with the operator if the original bytes are needed; it is not a
reason to delete any of the three present inputs.

## Canonicality rule

None of the files above is the source of truth for shipped behaviour. Use the
[documentation map](../README.md), current source/tests, immutable ledger, and
accepted decisions. Existing review and audit documents under this directory
remain historical evidence with their own status and exact revision boundaries.
