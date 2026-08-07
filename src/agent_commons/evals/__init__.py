"""Deterministic, privacy-safe workflow evaluation primitives.

The package deliberately has no provider dependency.  It is a small offline
regression layer for Agent Commons contracts, not a claim that unimplemented
workflow features are production-ready.
"""

from .catalog import CATALOG_VERSION, EVAL_CATALOG, catalog_cases, run_catalog
from .model import EvalCase, EvalResult, EvalStatus, MetricsAggregate

__all__ = [
    "CATALOG_VERSION",
    "EVAL_CATALOG",
    "EvalCase",
    "EvalResult",
    "EvalStatus",
    "MetricsAggregate",
    "catalog_cases",
    "run_catalog",
]
