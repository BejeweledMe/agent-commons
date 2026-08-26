"""Compatibility facade for the former root-level presentation module."""

from agent_commons.core.bounded import bounded_copy, truncate_utf8
from agent_commons.presentation.views import (
    addressed_spellings,
    inbox_view,
    orientation,
    render_views,
)

__all__ = [
    "addressed_spellings",
    "bounded_copy",
    "inbox_view",
    "orientation",
    "render_views",
    "truncate_utf8",
]
