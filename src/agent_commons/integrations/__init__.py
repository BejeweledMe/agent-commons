"""Project-local integrations for coding-agent clients."""

from .installer import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    SUPPORTED_INTEGRATIONS,
    FileChange,
    InstallationReport,
    initialize_workspace,
)

__all__ = [
    "MANAGED_BLOCK_END",
    "MANAGED_BLOCK_START",
    "SUPPORTED_INTEGRATIONS",
    "FileChange",
    "InstallationReport",
    "initialize_workspace",
]
