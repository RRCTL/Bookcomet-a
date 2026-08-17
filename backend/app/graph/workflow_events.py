"""In-process WebSocket fan-out for workflow run progress."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class WorkflowEventHub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, run_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms.setdefault(run_id, set()).add(ws)

    async def disconnect(self, run_id: str, ws: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.get(run_id)
            if not room:
                return
            room.discard(ws)
            if not room:
                del self._rooms[run_id]

    async def emit(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self._rooms.get(run_id, set()))
        if not sockets:
            return
        payload = json.dumps(event, default=str)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                room = self._rooms.get(run_id)
                if room:
                    for ws in dead:
                        room.discard(ws)

    async def snapshot(self, run_id: str, run_status: str, node_states: Any) -> None:
        await self.emit(
            run_id,
            {
                "type": "snapshot",
                "run_id": run_id,
                "run_status": run_status,
                "node_states_json": node_states,
            },
        )


workflow_event_hub = WorkflowEventHub()
