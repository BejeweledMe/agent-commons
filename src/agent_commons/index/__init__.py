"""Disposable projections over the authoritative file ledger."""

from .sqlite import IndexSyncResult, SQLiteIndex

__all__ = ["IndexSyncResult", "SQLiteIndex"]
