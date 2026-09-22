"""Start the Card SDK web UI (FastAPI) so templates can be viewed over HTTP.

Exposes the API defined in ``tool-ui/web/api.py``:

    GET /healthy/status                  health check
    GET /card/templates                  list available templates
    GET /student/photo/{student_id}      cached student photo
    GET /student/card/{side}/{id}        rendered front/back card SVG
    /docs                                Swagger UI (FastAPI)

Two import gotchas are handled automatically so the LOCAL dev SDK is used
(instead of any site-packages copy) and the web app's own static/templates
dirs resolve correctly:

    1. The SDK repo root is inserted at ``sys.path[0]``.
    2. The process chdirs to ``tool-ui/web`` before importing ``api``.

Run from the terminal UI, or standalone::

    python -m card_sdk.web_server                  # port 8000
    python -m card_sdk.web_server --port 9000
    python -m card_sdk.web_server --host 127.0.0.1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from typing import Optional

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _prepare_paths() -> pathlib.Path:
    """Put the local dev SDK + tool-ui/web on the right search/cwd paths.

    Returns the tool-ui/web directory (current working directory for the app).
    """
    sdk_root = pathlib.Path(__file__).resolve().parent.parent

    # 1) Local dev SDK wins over any site-packages install.
    if str(sdk_root) not in sys.path:
        sys.path.insert(0, str(sdk_root))

    # 2) The FastAPI app lives under tool-ui/web and resolves its own
    #    static/, templates/ and api_services/ relative to that directory.
    #    It may live in the standard location or the quarantined draf/ tree.
    candidates = [
        sdk_root / "tool-ui" / "web",
        sdk_root / "draf" / "tool-ui" / "web",
        sdk_root / "draf" / "root" / "tool-ui" / "web",
    ]
    web_dir = next((c for c in candidates if c.exists()), None)
    if web_dir is None:
        raise FileNotFoundError(
            "web UI directory not found; expected tool-ui/web (or draf/tool-ui/web)"
        )
    # make `import api` / `from api_services...` resolve regardless of cwd
    if str(web_dir) not in sys.path:
        sys.path.insert(0, str(web_dir))
    if pathlib.Path.cwd() != web_dir:
        os.chdir(web_dir)
    return web_dir


def serve_web(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, _test: bool = False):
    """Start the FastAPI web UI server (blocks until interrupted).

    Args:
        host: Bind host (default 0.0.0.0).
        port: Bind port (default 8000).
        _test: Internal — import-only mode used by the test suite.

    This is the synchronous entry point (uses ``uvicorn.run``). If it is
    called from inside an already-running event loop — for example from the
    async :func:`agent` flow — use :func:`serve_web_async` instead, otherwise
    ``uvicorn.run``'s internal ``asyncio.run`` raises
    "cannot be called from a running event loop".
    """
    _prepare_paths()

    import uvicorn
    import api  # now resolves local dev card_sdk + tool-ui/web imports

    _print_url(host, port)

    if _test:
        return api.web_App

    uvicorn.run(api.web_App, host=host, port=port)


async def serve_web_async(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Start the FastAPI web UI on the *current* event loop.

    Unlike :func:`serve_web` (which calls ``uvicorn.run`` and therefore needs
    its own event loop), this builds a uvicorn :class:`Server` and awaits its
    ``serve()`` coroutine on the caller's running loop. Use this from the
    async card-agent flow so the server keeps running without the
    "asyncio.run() cannot be called from a running event loop" error.
    """
    _prepare_paths()

    import uvicorn
    import uvicorn.config
    import api

    _print_url(host, port)

    config = uvicorn.config.Config(api.web_App, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()


def _print_url(host: str, port: int) -> None:
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    print(f"\n  Card SDK web UI ready  →  {url}")
    print(f"    templates            →  {url}/card/templates")
    print(f"    swagger docs         →  {url}/docs")
    print("    press Ctrl+C to stop\n", flush=True)


def _main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="card_sdk.web_server",
        description="Start the Card SDK web UI (template preview API).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    serve_web(host=args.host, port=args.port)


if __name__ == "__main__":
    _main()
