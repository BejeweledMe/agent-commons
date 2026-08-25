"""Compatibility imports for the pre-A3 standing-role module path.

Canonical role-domain rules now live in :mod:`agent_commons.domain.roles`.
Keep this module as a re-export during the migration window so existing callers
retain their stable import path without duplicating the implementation.
"""

from __future__ import annotations

from .roles import (
    AUTOMATIC_LEVEL_WITHHELD,
    CONTEXT_MODES,
    DENY_ALL,
    GRANT_LEVELS,
    GRANT_NAMES,
    NON_TERMINAL_DELEGATION_STATES,
    PROFILE_NARROWING,
    agent_delegations,
    descendants,
    effective_grants,
    grant_level,
    lineage,
    principals,
    prior_verdicts,
    retirement_blockers,
    session_agent_map,
    stored_grants,
    turnover_blockers,
    turnover_used,
)

__all__ = (
    "AUTOMATIC_LEVEL_WITHHELD",
    "CONTEXT_MODES",
    "DENY_ALL",
    "GRANT_LEVELS",
    "GRANT_NAMES",
    "NON_TERMINAL_DELEGATION_STATES",
    "PROFILE_NARROWING",
    "agent_delegations",
    "descendants",
    "effective_grants",
    "grant_level",
    "lineage",
    "principals",
    "prior_verdicts",
    "retirement_blockers",
    "session_agent_map",
    "stored_grants",
    "turnover_blockers",
    "turnover_used",
)
