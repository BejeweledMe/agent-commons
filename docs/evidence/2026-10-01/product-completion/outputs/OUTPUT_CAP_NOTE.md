# Output capture cap and binary artifact review

Reviewed G5/G7 source stays frozen: all 27 integration source SHA256 values still
match G5_G7_INTEGRATION.json. No cap or broker source was changed.

The broker's default capture policy is 1,048,576 bytes
(src/agent_commons/runtime/policy.py:71). `_BoundedOutput.consume` keeps only the
remaining prefix while counting every seen byte; `_drain` continues reading until
EOF (runtime/subprocess_runner.py:224, 304). Output truncation does not itself kill
the provider or disconnect its MCP tools. The ProcessResult declares truncation
(runtime/subprocess_runner.py:525).

Provider diagnostics decode only the bounded retained stdout; provider error events
after that prefix can be unavailable to classification
(runtime/diagnostics.py:437). This is an existing observability limit and may matter
for a large returned image or many build text entries. A 10 MiB image has about
13.34 MiB of base64 content if the provider echoes it to stdout.

Terminal authority is separate from captured stdout. The MCP guarded wrapper
records a terminal call and completion directly to TerminalToolAuditStore before
returning its tool response (mcp/server.py:528, 554). Delegation runtime joins that
store independently (services/delegation_runtime.py:752–775). Canonical finalization
also records review.completed and delegation.succeeded independently of stdout.
There is no retained-stdout terminal parser that must continue reading after the
capture cap to establish that result.

Separate synthetic probe output: `output-cap-probe.json`. It passed an image-like
base64 JSON event through the actual bounded capture class, then invoked the actual
scoped pixel read and review finalizer. Capture retained 1,048,576 of 1,398,244 seen
bytes and lost the subsequent synthetic terminal stdout marker. The terminal audit
still recorded exactly one call/completion, zero rejections; canonical outcome was
delegation.succeeded and exact result review was approved. This verifies the local
capture/audit separation and is not a real CLI/model transport or model quality test.

The root's real bounded binary artifact review after reinstall/canary remains the
provider gate: inspect actual process exit, canonical terminal outcome, independent
terminal audit and declared stdout truncation. Do not treat complete captured text
as a prerequisite for a correctly audited canonical terminal tool, or imply complete
diagnostic coverage when capture is truncated. No cap widening is proposed here.
