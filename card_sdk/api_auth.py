"""API-user authentication via a terminal QR-code handshake.

The admin API key is never hard-coded in the SDK. Instead, on every run the
SDK starts a tiny local HTTP endpoint, prints a scannable QR code (plus the
plain endpoint URL) in the terminal, and waits for the admin's web service
(WS) to POST the key back::

    POST /auth  ->  {"admin_api_key": "..."}              (JSON)
                ->  admin_api_key=...                     (form)

Once the submitted key passes a live probe against the Kaascan admin API the
session is *authorized* and card generation (SINGLE / MULTIPLE) may proceed.
The key is kept only in process memory and is used for the ``X-API-KEY``
header on every Kaascan admin call.

Endpoints::

    GET  /              -> tiny HTML authorize page (open in a phone browser)
    POST /auth          -> receive + validate ``admin_api_key``
    GET  /auth/status   -> JSON status of the current session

Run the handshake from code::

    import asyncio
    from card_sdk import api_auth

    async def main():
        server, session = api_auth.start_auth_server()
        endpoint = api_auth.session_endpoint(server)
        api_auth.print_terminal_qrcode(endpoint)
        key = await api_auth.await_authorization(session)
        server.shutdown()
        print("authorized:", bool(key))
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTH_HOST = os.environ.get("KAA_AUTH_HOST", "0.0.0.0")
AUTH_PORT = int(os.environ.get("KAA_AUTH_PORT", "8132"))
AUTH_TIMEOUT = int(os.environ.get("KAA_AUTH_TIMEOUT", "180"))

ADMIN_STUDENTS_URL = "https://api.v2.kaascan.com/admin/students"


# ---------------------------------------------------------------------------
# Process-wide authorized admin API key (shared with card_agent / cardfly).
# ---------------------------------------------------------------------------

_AUTHORIZED_API_KEY = ""
_AUTHORIZED_LOCK = threading.Lock()


def set_admin_api_key(key: str) -> None:
    """Remember the key that passed the handshake for this process."""
    global _AUTHORIZED_API_KEY
    with _AUTHORIZED_LOCK:
        _AUTHORIZED_API_KEY = (key or "").strip()


def get_admin_api_key() -> str:
    """The authorized admin API key (or ``KAA_SCAN_API_KEY`` env as fallback).

    Priority: (1) key authorised via the QR handshake this run, (2) the
    ``KAA_SCAN_API_KEY`` environment variable. Never a hard-coded value.
    """
    with _AUTHORIZED_LOCK:
        key = _AUTHORIZED_API_KEY
    if key:
        return key
    return (os.environ.get("KAA_SCAN_API_KEY") or "").strip()


def require_admin_api_key() -> bool:
    """True when a usable (authorized-or-env) admin API key is available."""
    return bool(get_admin_api_key())


# ---------------------------------------------------------------------------
# Validation against the real Kaascan admin API
# ---------------------------------------------------------------------------

def validate_admin_api_key(key: str) -> bool:
    """Probe the Kaascan admin API with ``key``; True when it is accepted.

    Behaviour:
    * API reachable → True only when the key returns a valid students payload.
    * API answers with an HTTP error (401/403 …) → False (key rejected).
    * API timing out / unreachable (offline machine) → a non-empty key is
      accepted, since it cannot be verified remotely — the caller prints a
      clear warning that validation could not be performed.
    """
    key = (key or "").strip()
    if not key:
        return False
    try:
        req = urllib.request.Request(
            ADMIN_STUDENTS_URL,
            headers={"accept": "application/json", "X-API-KEY": key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
            return isinstance(payload, dict) and "data" in payload
    except urllib.error.HTTPError as exc:
        # The API is reachable and refused the key — reject it.
        print(
            f"  [!] admin_api_key rejected by the Kaascan admin API "
            f"(HTTP {exc.code}).",
            flush=True,
        )
        return False
    except Exception:
        # Network unavailable (timeout / DNS / connection refused) — fall
        # back to accepting the key; the caller warns it could not verify it.
        print(
            "  [!] Could not reach the Kaascan admin API to verify the key "
            "— accepting it without remote validation.",
            flush=True,
        )
        return True


# ---------------------------------------------------------------------------
# Thread-safe handshake session
# ---------------------------------------------------------------------------

class AuthSession:
    """Holds the state of one authorization handshake (thread-safe)."""

    def __init__(self, token: str):
        self.token = token
        self._lock = threading.Lock()
        self.authorized = False
        self.admin_api_key = ""
        self.error = ""

    def authorize(self, admin_api_key: str) -> None:
        with self._lock:
            self.authorized = True
            self.admin_api_key = admin_api_key
            self.error = ""

    def reject(self, error: str) -> None:
        with self._lock:
            self.authorized = False
            self.error = error

    def is_authorized(self) -> bool:
        with self._lock:
            return self.authorized

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "authorized": self.authorized,
                "admin_api_key": self.admin_api_key,
                "error": self.error,
                "token": self.token,
            }


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def get_lan_ip() -> str:
    """Best-effort LAN IPv4 of this machine (so a phone on the same network)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def _pick_port(prefer: int) -> int:
    """``prefer`` if it is free, otherwise any OS-assigned port."""
    if prefer:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", prefer))
            probe.close()
            return prefer
        except OSError:
            probe.close()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_AUTH_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kaascan Card SDK — Authorize</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:420px;margin:6vh auto;padding:0 20px;color:#1a1a1a}
  h1{font-size:1.3rem}
  .card{border:1px solid #ddd;border-radius:12px;padding:20px}
  label{display:block;margin:12px 0 4px;font-size:.85rem;color:#555}
  input[type=password],input[type=text]{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;font-size:1rem;box-sizing:border-box}
  button{margin-top:16px;width:100%;padding:12px;border:0;border-radius:8px;background:#00897b;color:#fff;font-size:1rem;cursor:pointer}
  button:hover{background:#00695c}
  #r{margin-top:14px;font-size:.9rem}
  .ok{color:#1b7a2f}.bad{color:#b3261e}
</style>
</head>
<body>
<h1>Kaascan Card SDK</h1>
<div class="card">
  <p style="margin-top:0">Paste the <b>admin_api_key</b> to authorize this SDK
  session. It is only used in memory for card generation.</p>
  <form id="f">
    <label for="k">admin_api_key</label>
    <input type="password" id="k" name="admin_api_key" required autocomplete="off">
    <button type="submit">Authorize</button>
  </form>
  <div id="r"></div>
</div>
<script>
const f=document.getElementById('f'),r=document.getElementById('r');
f.addEventListener('submit',async e=>{
  e.preventDefault();
  r.className=''; r.textContent='Authorizing…';
  try{
    const res=await fetch('/auth',{
      method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({admin_api_key:f.admin_api_key.value})
    });
    const out=await res.json().catch(()=>({ok:false,error:'bad response'}));
    if(out.ok){r.className='ok';r.textContent='Authorized ✓ — you can close this page.';}
    else{r.className='bad';r.textContent='Failed: '+(out.error||'unknown error');}
  }catch(err){r.className='bad';r.textContent='Failed: '+err.message;}
});
</script>
</body>
</html>
"""


class _AuthHandler(BaseHTTPRequestHandler):
    server_version = "CardSDK-Auth/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # -- helpers ------------------------------------------------------------
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @property
    def session(self) -> AuthSession:
        return self.server.session  # type: ignore[attr-defined]

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path
        if route in ("/", "/auth"):
            self._send_html(_AUTH_PAGE)
            return
        if route == "/auth/status":
            self._send_json(200, {"ok": True, **self.session.snapshot()})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        route = urllib.parse.urlsplit(self.path).path
        if route != "/auth":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        raw = self._read_body().decode("utf-8", "replace") or ""
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype or raw.lstrip().startswith("{"):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
        else:
            payload = dict(urllib.parse.parse_qsl(raw))

        # A session token is embedded in the QR URL; reject mismatches.
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        supplied_token = qs.get("token", [""])[0]
        if supplied_token and supplied_token != self.session.token:
            self.session.reject("invalid session token")
            self._send_json(
                403,
                {"ok": False, "authorized": False, "error": "invalid session token"},
            )
            return

        admin_api_key = (payload.get("admin_api_key") or "").strip()
        if not admin_api_key:
            self.session.reject("admin_api_key is required")
            self._send_json(
                400,
                {"ok": False, "authorized": False, "error": "admin_api_key is required"},
            )
            return

        if not validate_admin_api_key(admin_api_key):
            self.session.reject("invalid admin_api_key")
            self._send_json(
                401,
                {"ok": False, "authorized": False, "error": "invalid admin_api_key"},
            )
            return

        self.session.authorize(admin_api_key)
        set_admin_api_key(admin_api_key)
        print("\n  [+] admin_api_key accepted — SDK authorized.\n", flush=True)
        self._send_json(200, {"ok": True, "authorized": True, "message": "authorized"})


def start_auth_server(
    host: str = AUTH_HOST, port: int = AUTH_PORT, token: str | None = None
):
    """Start the auth endpoint on a background thread.

    Returns ``(server, session)``. Call ``server.shutdown()`` when the
    handshake is done (success or abort).
    """
    ip = host if host not in ("0.0.0.0", "::", "") else None
    bind_host = ip or "0.0.0.0"
    real_port = _pick_port(port)
    token = token or secrets.token_hex(8)
    session = AuthSession(token)
    server = ThreadingHTTPServer((bind_host, real_port), _AuthHandler)
    server.session = session  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.daemon = True
    thread.start()
    return server, session


def session_endpoint(server, host: str | None = None) -> str:
    """A reachable ``/auth`` URL for this session (LAN IP when possible)."""
    port = server.server_address[1]
    host = host or get_lan_ip()
    return f"http://{host}:{port}/auth?token={server.session.token}"


def print_terminal_qrcode(url: str) -> None:
    """Render ``url`` as a block-style QR code straight in the terminal."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(tty=False)


def wait_for_authorization(
    session: AuthSession, timeout: float = AUTH_TIMEOUT, poll: float = 0.5
) -> str | None:
    """Block the calling thread until the WS posts a valid ``admin_api_key``.

    Returns the authorized key, or ``None`` when the handshake times out.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.is_authorized():
            return session.admin_api_key
        time.sleep(poll)
    return None


async def await_authorization(
    session: AuthSession, timeout: float = AUTH_TIMEOUT, poll: float = 0.5
) -> str | None:
    """Await the handshake from inside an async flow (e.g. ``Card.agent``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.is_authorized():
            return session.admin_api_key
        await asyncio.sleep(poll)
    return None