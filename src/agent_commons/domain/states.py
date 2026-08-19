"""Named delegation state sets shared across domain and adapters."""

NON_TERMINAL_DELEGATION_STATES = frozenset({"requested", "active", "input_needed"})
LIVE_WORKER_DELEGATION_STATES = frozenset({"active", "input_needed"})
