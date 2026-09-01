"""Project-local integrations for coding-agent clients."""

from .installer import (
    GROK_CONFIG_BLOCK_END,
    GROK_CONFIG_BLOCK_START,
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
    "GROK_CONFIG_BLOCK_END",
    "GROK_CONFIG_BLOCK_START",
    "SUPPORTED_INTEGRATIONS",
    "FileChange",
    "InstallationReport",
    "initialize_workspace",
]
