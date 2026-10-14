"""Q6 (decided 2026-10-14): a bearer token without a client_name identity.

Both refusals are ``authentication_missing`` with ``retryable=False``: the
agent cannot correct them, an operator must issue a token with a client_name
(specs/001-tool-guidance-remediation, tasks.md T067).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live_mem.core.live import LiveService
from live_mem.tools.guidance import ErrorKind


@pytest.mark.parametrize("agent", ["", None])
async def test_note_without_client_name_is_authentication_missing(agent):
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=True)
    storage.put = AsyncMock()
    with patch("live_mem.auth.context.get_current_agent_name", return_value=agent), patch(
        "live_mem.core.live.get_storage", return_value=storage
    ):
        result = await LiveService().write_note("p", "observation", "text")

    assert result["status"] == "error"
    assert result["message"] == "A non-empty client_name identity is required to write a note"
    assert result["error_kind"] == ErrorKind.AUTHENTICATION_MISSING.value
    assert result["remediation"]["retryable"] is False
    assert "client_name" in result["remediation"]["reason"]
    storage.put.assert_not_called()
