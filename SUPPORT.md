# Support policy

Agent Commons is alpha software. The immutable file-ledger core is the supported
product path; the local model broker is experimental, manually enabled, and not
covered by a production availability commitment.

Supported environments:

- macOS or Linux;
- CPython 3.11, 3.12, 3.13, or 3.14;
- a Git working tree with a writable operator state root;
- the core dependency set, or the matching `mcp`/`observability` extra when
  those optional surfaces are used.

Windows is not supported because durable coordination currently relies on
POSIX `fcntl.flock`. Unsupported hosts fail before state mutation with an
actionable diagnostic.

Before filing an issue, run `agent-commons --read-only doctor` and
`agent-commons support`. Share only their secret-free output, the package
version, OS/Python version, reproduction steps, and whether the optional broker
was enabled. Never attach ledger content, provider output, credentials, prompts,
or operational state wholesale.

Security-sensitive reports should not be opened publicly. Until a private
security contact is published, withhold exploit details and open a minimal
GitHub issue requesting a private reporting channel.
