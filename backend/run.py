"""
Windows-safe uvicorn launcher.

On Windows, the default ProactorEventLoop hangs on Ctrl+C with WinError 10054
(connection forcibly closed) because it cannot cleanly drain socket connections.
Switching to WindowsSelectorEventLoopPolicy before uvicorn creates its event loop
fixes the hang completely.
"""
import sys

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        timeout_graceful_shutdown=5,
    )
