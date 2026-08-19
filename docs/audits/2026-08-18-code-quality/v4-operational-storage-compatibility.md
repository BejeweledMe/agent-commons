# V4 operational storage compatibility check

Checked on 2026-08-19 after strict serialization was enabled for new writes and
before strict parsing was enabled for reads.

The read-only scan covered every `*.json` and `*.jsonl` file under the three
operational state roots available on the audit host:

- `.git/agent-commons-state`;
- `/Users/dmitrijersov/.agent-commons-state`;
- `/Users/dmitrijersov/.local/state/agent-commons`.

Results:

- files parsed: **8,110**;
- JSONL records parsed separately: **104**;
- strict-parser failures: **0**.

The strict parser rejects non-finite numbers and duplicate object keys. A JSON
document cannot retain the runtime type of an object key: all keys are strings
on disk. The observable hazard from the old serializer is therefore a collision
such as Python keys `1` and `"1"`, which produces duplicate JSON keys; none were
found. Static inspection also found that the operational envelopes are built
from named string fields. Communication metadata is the one caller-supplied
nested mapping, and the strict writer regression exercises it directly.

This evidence supports enabling strict reads without a migration window. It
does not claim that an unavailable state root was scanned; any such root will
fail closed when first read by the strict parser.
