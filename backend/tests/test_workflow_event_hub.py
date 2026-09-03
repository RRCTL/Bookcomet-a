from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketState

from app.graph.workflow_events import WorkflowEventHub


@pytest.mark.asyncio
async def test_connect_skips_accept_when_already_connected() -> None:
    hub = WorkflowEventHub()
    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.accept = AsyncMock()

    await hub.connect("run-1", ws)

    ws.accept.assert_not_awaited()
    assert ws in hub._rooms["run-1"]


@pytest.mark.asyncio
async def test_connect_accepts_when_not_connected() -> None:
    hub = WorkflowEventHub()
    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTING
    ws.accept = AsyncMock()

    await hub.connect("run-2", ws)

    ws.accept.assert_awaited_once()
    assert ws in hub._rooms["run-2"]
