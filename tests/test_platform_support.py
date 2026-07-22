from __future__ import annotations

import pytest

from agent_commons import platform_support
from agent_commons.errors import ConfigurationError


def test_unsupported_platform_fails_with_action_before_lock_use(monkeypatch) -> None:
    monkeypatch.setattr(platform_support, "_fcntl", None)

    with pytest.raises(ConfigurationError, match="supports macOS and Linux only") as captured:
        platform_support.require_supported_platform()

    assert "supported host or container" in str(captured.value)
