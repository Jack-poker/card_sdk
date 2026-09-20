"""CardFly Studio server — binds the visual template editor to the SDK.

Serves the CardFly editor at ``http://<host>:<port>/`` and points its live
``templates/`` folder at the SDK's real ``templates_base/`` directory, so a
developer can list, open, customise and *save* templates straight back into the
SDK. Any template saved here becomes immediately usable by
``pull_template_options()`` / ``generate_card()``.

Endpoints (all JSON unless noted)::

    GET  /                         → CardFly editor (index.html)
    GET  /templates/$name/         → directory listing of a template folder
    GET  /templates/$name/$file    → raw front/back .kaascan (SVG) content
    GET  /template-list             → JSON list of template names (SDK live)
    POST /save-template            → write front/back into templates_base
    DELETE /template/$name          → remove a template folder from templates_base

The editor already speaks the ``templates/<name>/`` HTML-listing dialect, so it
works with zero editor changes for *reading*; the new ``Save to SDK`` button
calls ``POST /save-template``.

Run it::

    python -m card_sdk.cardfly_server             # port 8131 (CardFly default)
    python -m card_sdk.cardfly_server --port 9000

Then open http://127.0.0.1:8131/
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SDK_ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES_BASE = SDK_ROOT / "card_sdk" / "templates" / "templates_base"
EDITOR_DIR = SDK_ROOT / "CARD_FLY" / "editor"
# Reusable component presets (headers, stamps, signatures, logos, barcodes…)
# live outside templates_base so they can't be mistaken for card templates.
COMPONENTS_DIR = SDK_ROOT / "card_sdk" / "components"

# Kaascan admin API (proxied so the browser never needs the API key / CORS).
# The API key is never hard-coded: it comes from the runtime QR authorization
# handshake (card_sdk.api_auth) or the KAA_SCAN_API_KEY environment variable.
ADMIN_STUDENTS_URL = "https://api.v2.kaascan.com/admin/students"
ADMIN_SCHOOLS_URL = "https://automation.kaascan.com/webhook/schools"

from card_sdk.api_auth import get_admin_api_key

_ADMIN_TIMEOUT = 40

# Where a template editor may live if it has been quarantined into draf/.
EDITOR_DIR_CANDIDATES = [
    SDK_ROOT / "CARD_FLY" / "editor",
    SDK_ROOT / "draf" / "root" / "CARD_FLY" / "editor",
]

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".kaascan": "image/svg+xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".map": "application/json; charset=utf-8",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

STATIC_INDEX = "index.html"

# A template folder is valid when it holds a front file. Names we treat as the
# editable faces inside a template folder.
FRONT_RE = re.compile(r"^front\.", re.IGNORECASE)
BACK_RE = re.compile(r"^back\.", re.IGNORECASE)
CARD_EXT = re.compile(r"\.(svg|kaascan)$", re.IGNORECASE)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9 _\-()\[\].&+]+$")


def _resolve_editor_dir() -> pathlib.Path:
    for cand in EDITOR_DIR_CANDIDATES:
        if cand.is_dir() and (cand / STATIC_INDEX).exists():
            return cand
    raise FileNotFoundError(
        "CardFly editor not found; expected CARD_FLY/editor (or draf/root/CARD_FLY/editor)"
    )


def _send_json(handler, code: int, payload) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler, code: int, message: str) -> None:
    _send_json(handler, code, {"ok": False, "error": message})


def _safe_template_name(name: str) -> str | None:
    name = (name or "").strip()
    if not name or not _SAFE_NAME.match(name):
        return None
    if name in (".", "..") or "/" in name or "\\" in name:
        return None
    return name


def _template_meta(tfolder: pathlib.Path) -> dict:
    front = back = None
    for f in sorted(tfolder.iterdir()):
        if not f.is_file() or not CARD_EXT.search(f.name):
            continue
        if FRONT_RE.match(f.name):
            front = f.name
        elif BACK_RE.match(f.name):
            back = f.name
    if front is None:
        for f in sorted(tfolder.iterdir()):
            if f.is_file() and CARD_EXT.search(f.name):
                front = f.name
                break
    return {"name": tfolder.name, "front": front, "back": back}


def _list_templates() -> list[dict]:
    if not TEMPLATES_BASE.is_dir():
        return []
    out = []
    for d in sorted(TEMPLATES_BASE.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            out.append(_template_meta(d))
    return out


def _rebuild_templates_json() -> None:
    """Rewrite templates.json from the real folder state (authoritative scan)."""
    templates = _list_templates()
    entry = {
        "template_file": [
            {
                "filename": t["name"],
                "file_path": str(TEMPLATES_BASE / t["name"]),
                "front": str(TEMPLATES_BASE / t["name"] / t["front"]) if t["front"] else "",
                "back": str(TEMPLATES_BASE / t["name"] / t["back"]) if t["back"] else "",
            }
            for t in templates
        ]
    }
    try:
        TEMPLATES_BASE.mkdir(parents=True, exist_ok=True)
        (TEMPLATES_BASE / "templates.json").write_text(
            json.dumps([entry], indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _write_template(name: str, files: list[dict]) -> dict:
    """Write a template's front/back into templates_base and re-register it."""
    tfolder = TEMPLATES_BASE / name
    tfolder.mkdir(parents=True, exist_ok=True)
    written = []
    for item in files:
        side = (item.get("side") or "").strip().lower()
        content = item.get("content")
        fname = item.get("filename")
        if not content:
            continue
        if side in ("front", "back") and CARD_EXT.search((fname or "")):
            target = tfolder / fname
        elif fname and _safe_template_name(fname):
            target = tfolder / fname
        else:
            continue
        target.write_text(content, encoding="utf-8")
        written.append(target.name)
    _rebuild_templates_json()
    return {"name": name, "written": written, "front": _template_meta(tfolder)["front"]}


def _copy_tree(src: pathlib.Path, dst: pathlib.Path) -> None:
    import shutil
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_dir():
            _copy_tree(f, dst / f.name)
        else:
            (dst / f.name).write_bytes(f.read_bytes())


def _duplicate_template(src_name: str, new_name: str) -> dict:
    src = TEMPLATES_BASE / src_name
    dst = TEMPLATES_BASE / new_name
    if not src.is_dir():
        raise FileNotFoundError("Source template not found: " + src_name)
    if dst.exists():
        raise OSError("A template named '%s' already exists" % new_name)
    _copy_tree(src, dst)
    _rebuild_templates_json()
    return {"name": new_name, "from": src_name, "front": _template_meta(dst)["front"]}


def _rename_template(old_name: str, new_name: str) -> dict:
    src = TEMPLATES_BASE / old_name
    dst = TEMPLATES_BASE / new_name
    if not src.is_dir():
        raise FileNotFoundError("Template not found: " + old_name)
    if dst.exists():
        raise OSError("A template named '%s' already exists" % new_name)
    src.rename(dst)
    # fix any references inside the files that used the old folder name
    for f in dst.rglob("*"):
        if f.is_file() and CARD_EXT.search(f.name):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if old_name in text:
                f.write_text(text.replace(old_name, new_name), encoding="utf-8")
    _rebuild_templates_json()
    return {"name": new_name, "from": old_name, "front": _template_meta(dst)["front"]}


# ---------------------------------------------------------------------------
# Reusable component presets (headers, stamps, signatures, logos, barcodes…)
# ---------------------------------------------------------------------------

def _components_dir() -> pathlib.Path:
    COMPONENTS_DIR.mkdir(parents=True, exist_ok=True)
    return COMPONENTS_DIR


def _safe_component_name(name: str) -> str | None:
    name = (name or "").strip()
    if not name or not _SAFE_NAME.match(name):
        return None
    if name in (".", "..") or "/" in name or "\\" in name:
        return None
    return name


def _list_components() -> list[dict]:
    d = COMPONENTS_DIR
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".svg":
            continue
        meta_path = f.with_suffix(".json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        out.append({
            "name": f.name,
            "title": meta.get("title", f.stem),
            "category": meta.get("category", "general"),
            "categories": meta.get("categories", []),
            "kind": meta.get("kind", ""),
        })
    return out


def _save_component(name: str, svg: str, meta: dict) -> dict:
    d = _components_dir()
    name = _safe_component_name(name)
    if not name or not name.lower().endswith(".svg"):
        # normalise to a .svg filename
        name = (_safe_component_name(name) or "component").rstrip(".svg") + ".svg"
    (d / name).write_text(svg, encoding="utf-8")
    meta_path = d / name.replace(".svg", ".json")
    meta_payload = {
        "title": meta.get("title", name[:-4]),
        "category": meta.get("category", "general"),
        "categories": meta.get("categories", []),
        "kind": meta.get("kind", ""),
        "variables": meta.get("variables", []),
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    return {"name": name, **meta_payload}


# ---------------------------------------------------------------------------
# Kaascan admin proxy — fetch schools + students for live card previews
# ---------------------------------------------------------------------------

def _admin_get(url: str, *, headers: dict | None = None, timeout: int = _ADMIN_TIMEOUT) -> dict | list:
    """GET a Kaascan admin endpoint and return the parsed JSON."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_schools() -> list[dict]:
    """Return the list of school names from the Kaascan webhook."""
    try:
        data = _admin_get(ADMIN_SCHOOLS_URL, timeout=15)
    except Exception as exc:
        raise OSError("Could not reach the Kaascan schools feed: %s" % exc)
    names = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("school_name")
            else:
                name = entry
            name = (name or "").strip()
            if name and name not in names:
                names.append({"name": name})
    elif isinstance(data, dict):
        # {"school_name": "..."} single-school response
        name = (data.get("school_name") or "").strip()
        if name:
            names.append({"name": name})
    return names


def _fetch_students(school: str | None = None) -> list[dict]:
    """Return student records from the Kaascan admin API, optionally filtered
    by school name (matched against each record's ``school_name``)."""
    headers = {"accept": "application/json", "X-API-KEY": get_admin_api_key()}
    try:
        payload = _admin_get(ADMIN_STUDENTS_URL, headers=headers)
    except Exception as exc:
        raise OSError("Could not reach the Kaascan admin API: %s" % exc)
    if not isinstance(payload, dict):
        raise OSError("Unexpected response from the Kaascan admin API")
    records = payload.get("data") or []
    out = []
    want = (school or "").strip().lower()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if want and (rec.get("school_name") or "").strip().lower() != want:
            continue
        out.append({
            "student_id": rec.get("student_id", ""),
            "student_name": rec.get("student_name", ""),
            "student_class": rec.get("grade", ""),
            "school_name": rec.get("school_name", ""),
            "student_photo_url": rec.get("student_photo_url", ""),
        })
    return out


def _clean_photo_url(raw: str) -> str:
    """Trim stray non-URL trailing characters (the feed sometimes ends with '.')."""
    raw = (raw or "").strip()
    raw = re.sub(r"[^A-Za-z0-9:/#&?=._%+-]+$", "", raw)
    return raw


def _fetch_photo(url: str) -> tuple[bytes, str]:
    """Fetch a student photo server-side (bypassing CORS) and return bytes + MIME."""
    url = _clean_photo_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "cardfly-sdk/1.0"})
    with urllib.request.urlopen(req, timeout=_ADMIN_TIMEOUT) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip().lower()
    mime = ctype if ctype in ("image/png", "image/jpeg", "image/gif", "image/webp") else "image/png"
    return data, mime


def _student_qr_data_uri(payload: str) -> str:
    """A Kaascan-style QR PNG data URI for the given student id payload."""
    from card_sdk.base64qrcode import base64_qrcode
    return base64_qrcode(payload, selected_style="4", front_color="0011ffff")


class _Handler(BaseHTTPRequestHandler):
    server_version = "CardFly-Server/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet-ish
        pass

    # -- helpers -------------------------------------------------------------
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _send_file(self, path: pathlib.Path) -> None:
        data = path.read_bytes()
        ext = path.suffix.lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        if path.name == STATIC_INDEX:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- routing -------------------------------------------------------------
    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path
        route = urllib.parse.unquote(route)

        if route in ("/", "/index.html"):
            idx = _resolve_editor_dir() / STATIC_INDEX
            if idx.exists():
                self._send_file(idx)
            else:
                _send_error(self, 404, "CardFly editor not found")
            return

        if route == "/health":
            _send_json(self, 200, {"ok": True, "templates_base": str(TEMPLATES_BASE)})
            return

        if route == "/template-list":
            _send_json(self, 200, {"ok": True, "templates": _list_templates()})
            return

        # Component presets library (reusable headers, stamps, signatures…)
        if route == "/components":
            _send_json(self, 200, {"ok": True, "components": _list_components()})
            return
        if route.startswith("/components/"):
            cname = urllib.parse.unquote(route.partition("/components/")[2])
            target = (COMPONENTS_DIR / cname).resolve()
            if not str(target).startswith(str(COMPONENTS_DIR.resolve())) or not target.is_file():
                _send_error(self, 404, "Component not found")
                return
            self._send_file(target)
            return

        # Kaascan admin proxy (live schools / students for card previews)
        if route == "/admin/schools":
            try:
                _send_json(self, 200, {"ok": True, "schools": _fetch_schools()})
            except OSError as exc:
                _send_error(self, 502, str(exc))
            return
        if route == "/admin/students":
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            school = qs.get("school", [None])[0]
            try:
                students = _fetch_students(school)
            except OSError as exc:
                _send_error(self, 502, str(exc))
                return
            _send_json(self, 200, {"ok": True, "count": len(students), "students": students})
            return

        # Proxy a student photo as bytes so the browser never hits Kaascan CORS.
        if route == "/admin/photo":
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            url = qs.get("url", [""])[0]
            if not url:
                _send_error(self, 400, "Missing ?url=")
                return
            try:
                data, mime = _fetch_photo(url)
            except Exception as exc:
                _send_error(self, 502, "Could not fetch photo: %s" % exc)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=600")
            self.end_headers()
            self.wfile.write(data)
            return

        # QR data URI for a student id payload (used to fill {data_qrcode}).
        if route == "/admin/qr":
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            text = qs.get("text", [""])[0]
            if not text:
                _send_error(self, 400, "Missing ?text=")
                return
            try:
                uri = _student_qr_data_uri(text)
            except Exception as exc:
                _send_error(self, 502, "Could not generate QR: %s" % exc)
                return
            _send_json(self, 200, {"ok": True, "qr": uri})
            return

        # Static editor assets (everything else that isn't a template route).
        if not route.startswith("/templates/") and not route.startswith("/template/"):
            self._serve_static(route)
            return

        # /templates/<name>/...  → live SDK templates_base
        self._serve_template_route(route)

    def _serve_static(self, route: str) -> None:
        editor = _resolve_editor_dir()
        # Prevent path traversal outside the editor dir.
        rel = route.lstrip("/").split("?", 1)[0]
        target = (editor / rel).resolve()
        if not str(target).startswith(str(editor.resolve())) or not target.is_file():
            _send_error(self, 404, "Not found")
            return
        self._send_file(target)

    def _listing_html(self, entries, dirs_only: bool) -> bytes:
        """HTML listing in the dialect the CardFly editor parses.

        ``dirs_only=True`` (the /templates/ root) emits only directory entries,
        each with a trailing '/', which is what the editor's Templates menu
        expects. ``dirs_only=False`` (a template folder) emits bare file names
        so the editor's front/back regex still matches (no trailing '/' on files).
        """
        items = []
        for d, name in entries:
            is_dir = d.is_dir()
            if dirs_only and not is_dir:
                continue
            if dirs_only:
                shown = name + "/"
            else:
                shown = name
            items.append(
                '<a href="%s">%s</a>' % (urllib.parse.quote(shown), urllib.parse.quote(shown))
            )
        return "\n".join(items).encode("utf-8")

    def _html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_template_route(self, route: str) -> None:
        parts = [p for p in route.split("/") if p]
        # /templates/<name>/  or  /templates/<name>/<file>
        if route.startswith("/templates/"):
            if len(parts) < 2:
                # Root template folder → list template directories (trailing /).
                root_entries = sorted(
                    (d, d.name) for d in TEMPLATES_BASE.iterdir() if d.is_dir() and not d.name.startswith(".")
                )
                self._html(self._listing_html(root_entries, dirs_only=True))
                return
            name = parts[1]
            tfolder = (TEMPLATES_BASE / name).resolve()
            if not str(tfolder).startswith(str(TEMPLATES_BASE.resolve())) or not tfolder.is_dir():
                _send_error(self, 404, "Template not found: " + name)
                return
            if len(parts) == 2:
                # Template folder listing → bare file names (front/back regexes).
                file_entries = sorted((f, f.name) for f in tfolder.iterdir() if f.is_file())
                self._html(self._listing_html(file_entries, dirs_only=False))
                return
            # /templates/<name>/<file>
            fname = parts[2]
            target = (tfolder / fname).resolve()
            if not str(target).startswith(str(tfolder.resolve())) or not target.is_file():
                _send_error(self, 404, "File not found")
                return
            self._send_file(target)
            return
        _send_error(self, 404, "Not found")

    def do_POST(self):
        route = urllib.parse.urlsplit(self.path).path
        route = urllib.parse.unquote(route)

        try:
            payload = json.loads(self._read_body().decode("utf-8") or "{}")
        except Exception:
            _send_error(self, 400, "Invalid JSON body")
            return

        if route == "/save-template":
            name = _safe_template_name(payload.get("name"))
            if not name:
                _send_error(self, 400, "Template name is required and must be safe")
                return
            files = payload.get("files") or []
            if not files:
                _send_error(self, 400, "No files provided")
                return
            try:
                result = _write_template(name, files)
            except OSError as exc:
                _send_error(self, 500, "Could not write template: %s" % exc)
                return
            _send_json(self, 200, {"ok": True, **result})
            return

        if route == "/duplicate-template":
            src = _safe_template_name(payload.get("from"))
            new = _safe_template_name(payload.get("name") or payload.get("to"))
            if not src or not new:
                _send_error(self, 400, "Both source and new template name are required")
                return
            try:
                result = _duplicate_template(src, new)
            except (OSError, FileNotFoundError) as exc:
                _send_error(self, 400 if isinstance(exc, OSError) else 404, str(exc))
                return
            _send_json(self, 200, {"ok": True, **result})
            return

        if route == "/rename-template":
            old = _safe_template_name(payload.get("from"))
            new = _safe_template_name(payload.get("to") or payload.get("name"))
            if not old or not new:
                _send_error(self, 400, "Both old and new template name are required")
                return
            try:
                result = _rename_template(old, new)
            except (OSError, FileNotFoundError) as exc:
                _send_error(self, 400 if isinstance(exc, OSError) else 404, str(exc))
                return
            _send_json(self, 200, {"ok": True, **result})
            return

        if route == "/save-component":
            name = payload.get("name")
            svg = payload.get("svg")
            if not name or not svg:
                _send_error(self, 400, "Component name and svg are required")
                return
            try:
                result = _save_component(name, svg, payload.get("meta") or {})
            except OSError as exc:
                _send_error(self, 500, "Could not save component: %s" % exc)
                return
            _send_json(self, 200, {"ok": True, **result})
            return

        _send_error(self, 404, "Not found")

    def do_DELETE(self):
        route = urllib.parse.urlsplit(self.path).path
        route = urllib.parse.unquote(route)
        parts = [p for p in route.split("/") if p]
        import shutil

        # /template/<name>
        if len(parts) == 2 and parts[0] == "template":
            name = parts[1]
            tfolder = (TEMPLATES_BASE / name).resolve()
            if not str(tfolder).startswith(str(TEMPLATES_BASE.resolve())) or not tfolder.is_dir():
                _send_error(self, 404, "Template not found")
                return
            shutil.rmtree(tfolder)
            _rebuild_templates_json()
            _send_json(self, 200, {"ok": True, "deleted": name})
            return

        # /components/<name>  (delete a component preset)
        if len(parts) == 2 and parts[0] == "components":
            cname = parts[1]
            target = (COMPONENTS_DIR / cname).resolve()
            if not str(target).startswith(str(COMPONENTS_DIR.resolve())) or not target.is_file():
                _send_error(self, 404, "Component not found")
                return
            target.unlink()
            meta = target.with_suffix(".json")
            if meta.exists():
                meta.unlink()
            _send_json(self, 200, {"ok": True, "deleted": cname})
            return

        _send_error(self, 404, "Not found")


def serve_impl(host: str, port: int, quiet: bool = False) -> None:
    _resolve_editor_dir()  # fail fast if the editor is missing
    TEMPLATES_BASE.mkdir(parents=True, exist_ok=True)
    _rebuild_templates_json()
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    if not quiet:
        print(f"\n  CardFly Studio  →  {url}")
        print(f"    editor        →  {url}/")
        print(f"    live templates→  {TEMPLATES_BASE}")
        print("    press Ctrl+C to stop\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="card_sdk.cardfly_server",
        description="Bind CardFly editor to the SDK templates_base with save support.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8131, help="bind port (default 8131)")
    args = parser.parse_args(argv)
    serve_impl(host=args.host, port=args.port)


if __name__ == "__main__":
    _main()
