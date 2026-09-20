"""cardgen — the CardFly card-generator bridge.

Put a single decorator on a card generator function and CardFly Studio will
automatically read the variables that function needs to fill in on the card.

It works in three small steps:

    1. You decorate a generator function:

           from cardgen import card, Field, Image

           @card(name="Student Card", template="front.card.kaascan")
           def generate_student(n: int, full_name: str,
                                id_number: Field(str, label="Student ID", required=True),
                                photo: Image, grade: Choice("A", "B", "C")) -> str:
               ...

    2. cardgen inspects that function's signature (names, defaults, type hints,
       annotations and Field metadata) and turns it into a machine-readable
       JSON contract of the variables the card interface must expose.

    3. When you run the script, cardgen automatically starts a tiny local HTTP
       listener. The browser app listens on that same address, fetches the
       contract, and pre-fills the editor's "Variables" tab for you.

That last bit is why there is no "emit a file then import it" step — the app
and your generator talk to each other over HTTP on your machine.
"""

from __future__ import annotations

import argparse
import functools
import html as _html
import inspect
import json
import os
import sys
import threading
import typing
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

__all__ = ["card", "Field", "Image", "Choice", "serve"]

DEFAULT_PORT = 8123
ENDPOINT = "/__card__variable_contract__"
DOCS_ENDPOINT = "/__card__docs__"


# ---------------------------------------------------------------------------
# Variable metadata helpers (used as default values / annotations)
# ---------------------------------------------------------------------------

class Image:
    """Marker for an image variable (photo, logo, signature…).

    Used as a type annotation or default:
        @card()
        def gen(photo: Image): ...
        def gen(photo=Image): ...      # same meaning
    """


class Choice(list):
    """Marker for a variable that must be one of a fixed set of values.

        grade: Choice("A", "B", "C")
    """

    def __init__(self, *values):
        super().__init__(values)


@dataclass
class Field:
    """Rich metadata for a card variable.

        name: str             # optional; defaults to the parameter name
        type: Any             # str | int | float | bool | Image | Choice[...]
        label: str            # human friendly label shown in the editor
        default: Any          # default sample value
        required: bool        # card is not valid without this value
        help: str             # short description shown in the editor
        order: int            # position in the Variables tab
        options: List[str]    # allowed values (for choices)
    """

    type: Any = str
    label: str = ""
    default: Any = None
    required: bool = True
    help: str = ""
    order: int = 0
    options: Optional[List[str]] = None

    def __init__(self, type: Any = str, *, label: str = "", default: Any = None,
                 required: bool = True, help: str = "", order: int = 0,
                 options: Optional[List[str]] = None):
        self.type = type
        self.label = label
        self.default = default
        self.required = required
        self.help = help
        self.order = order
        self.options = options


# ---------------------------------------------------------------------------
# Contract building
# ---------------------------------------------------------------------------

def _KNOWN_TYPES():
    return {str: "Text", int: "Text", float: "Text", bool: "Text", Image: "Image"}


def _kind_of(t, default_value) -> str:
    g = _KNOWN_TYPES()
    # Choice markers
    if isinstance(t, Choice):
        return "Choice"
    try:
        if t in g:
            return g[t]
    except (TypeError, KeyError):
        pass
    # string-ish defaults imply text
    if isinstance(t, str) and t in g:
        return g[t]
    if isinstance(default_value, Image) or t is Image or default_value is Image:
        return "Image"
    if isinstance(default_value, bool):
        return "Text"
    # fall back to text
    return "Text"


def build_contract(func, *, name=None, description="", template="",
                   require_all=False) -> Dict[str, Any]:
    """Inspect `func` and return the JSON-able variable contract."""
    sig = inspect.signature(func)
    hints = {}
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        pass

    params: List[Dict[str, Any]] = []
    order_seen = 0

    for pname, p in sig.parameters.items():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue  # skip *args / **kwargs
        if pname.startswith("_"):
            continue  # reserved/ignored

        meta: Optional[Field] = None
        annotation = p.annotation if p.annotation is not inspect.Parameter.empty else hints.get(pname)

        # Field(...) may appear as a default OR as the annotation itself.
        if isinstance(p.default, Field):
            meta = p.default
            ann_for_type = meta.type or annotation
        elif isinstance(annotation, Field):
            meta = annotation
            ann_for_type = meta.type or str
        else:
            ann_for_type = annotation

        # A Choice used as a default or annotation.
        options = None
        if isinstance(p.default, Choice):
            ann_for_type = Choice
            options = list(p.default)
        if isinstance(ann_for_type, Choice):
            options = options or list(ann_for_type)

        has_default = p.default is not inspect.Parameter.empty and not isinstance(p.default, (Field, Choice))
        default_val = None if has_default is False else _default(meta, p.default, ann_for_type)

        kind = _kind_of(ann_for_type, default_val) if not options else "Choice"

        params.append({
            "name": pname,
            "type": kind,
            "label": _label(meta, pname),
            "required": _required(meta, has_default, require_all),
            "default": default_val,
            "help": (meta.help if meta else "") or "",
            "order": meta.order if (meta and meta.order) else order_seen,
            "options": options or (list(meta.options) if meta and meta.options else None),
        })
        order_seen += 1

    return {
        "schema": "cardgen/v1",
        "card": name or getattr(func, "__name__", "card"),
        "description": (description or _first_doc_line(func)) or "",
        "template": template or "",
        "variables": params,
    }


def _first_doc_line(func):
    if not func.__doc__:
        return ""
    return func.__doc__.strip().split("\n")[0]


def _default(meta, raw_default, ann_for_type):
    if meta and meta.default is not None:
        return meta.default
    if raw_default is None or isinstance(raw_default, bool):
        return raw_default if raw_default is not None else False
    if isinstance(raw_default, (int, float, str)):
        # skip inspect.Signature.empty sentinel-like
        if raw_default is inspect.Parameter.empty:
            return None
        return raw_default
    return None


def _label(meta, pname) -> str:
    if meta and meta.label:
        return meta.label
    nice = pname.replace("_", " ").strip()
    return nice[:1].upper() + nice[1:] if nice else pname


def _required(meta, has_default, require_all) -> bool:
    if require_all:
        return True
    if meta:
        return meta.required
    return not has_default


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def card(*, name: Optional[str] = None, description: str = "", template: str = "",
         require_all: bool = False, port: Optional[int] = None):
    """Decorator for a card generator function.

    Marks a function as a CardFly card generator and remembers the variables
    it accepts so the editor can build the card interface automatically.

        @card(name="Student Card", template="front.card.kaascan")
        def generate(full_name: str, photo: Image, grade: Choice("A", "B")) -> str:
            ...

    Returns the original function unchanged — you can still call it directly
    from Python. The only side effect is that calling `serve()` (or running the
    module with `python cardgen.py your_script.py`) exposes the contract over
    HTTP for the browser app.

    Keyword arguments
    -----------------
    name : str, optional
        Friendly card name. Defaults to the function name.
    description : str, optional
        One-line description of the card.
    template : str, optional
        The .kaascan / .svg template this generator produces.
    require_all : bool, default False
        Mark every variable required, even ones with a default.
    port : int, optional
        Port for the local HTTP listener. Defaults to CARDGEN_PORT or 8123.
    """
    def deco(func):
        var_holder = {
            "contract": None,
            "port": port,
            "_name": name,
            "_desc": description,
            "_template": template,
            "_require_all": require_all,
        }

        @functools.wraps(func)
        def _wrapped(*a, **k):
            return func(*a, **k)

        # stash metadata on the wrapper + original for introspection
        _wrapped.__cardgen__ = var_holder
        _wrapped.__cardgen_contract__ = None
        _wrapped.__cardgen_build__ = (
            lambda: build_contract(
                func, name=name, description=description, template=template,
                require_all=require_all,
            )
        )

        # also register globally so serve() (run as a script) can find it
        _REGISTRY.append(_wrapped)
        return _wrapped

    return deco


_REGISTRY: List[Any] = []
_CONTRACT_CACHE: List[Dict[str, Any]] = []
_LOADED_MODULES: List[Any] = []


# ---------------------------------------------------------------------------
# HTTP listener
# ---------------------------------------------------------------------------

_CARD_SNIPPET = '''\
from cardgen import card, Field, Image, Choice, serve

@card(name="Student Card", template="front.card.kaascan")
def generate_student_card(
    full_name: str,
    id_number: Field(str, label="Student ID", required=True, help="Unique ID."),
    year: int = 2025,
    photo: Image = Image,
    grade: Choice("A", "B", "C") = Choice("A", "B", "C"),
) -> str:
    return '<svg ...>{full_name}</svg>'

if __name__ == "__main__":
    serve()
'''


def _docs_html(port: int = DEFAULT_PORT) -> str:
    escaped_snippet = _html.escape(_CARD_SNIPPET)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cardgen bridge — CardFly</title>
<style>
  :root {{ --ink:#14181f; --yellow:#ffd02e; --card:#fffdf5; --mut:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:"Outfit",system-ui,Segoe UI,Roboto,sans-serif; background:#f4f2ea; color:var(--ink); line-height:1.55; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:40px 24px 80px; }}
  .top {{ display:flex; align-items:center; gap:14px; margin-bottom:28px; }}
  .logo {{ width:46px;height:46px;border-radius:12px;background:var(--yellow);border:2px solid var(--ink);
          display:grid;place-items:center;font-weight:900;box-shadow:4px 4px 0 var(--ink); }}
  .top h1 {{ margin:0;font-size:26px; }}
  .top p {{ margin:2px 0 0; color:var(--mut); font-size:13px; }}
  .card {{ background:var(--card); border:2px solid var(--ink); border-radius:14px;
          box-shadow:6px 6px 0 rgba(20,24,31,.08); padding:22px 24px; margin:18px 0; }}
  .card h2 {{ margin-top:0; font-size:17px; letter-spacing:.02em; text-transform:uppercase; }}
  .route {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; background:#0e1422; color:#7ee0ff;
           border-radius:6px; padding:2px 7px; }}
  pre {{ background:#0e1422; color:#e6edf7; border-radius:10px; padding:16px; overflow:auto;
        font-size:13px; line-height:1.5; }}
  code {{ font-family:ui-monospace,Menlo,Consolas,monospace; background:#eef0f4; padding:1px 5px;border-radius:4px;font-size:.92em; }}
  pre code {{ background:none; padding:0; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }}
  .pill {{ display:inline-block; background:var(--yellow); border:1px solid var(--ink); border-radius:999px;
          padding:1px 9px; font-size:11px; font-weight:800; }}
  ul {{ margin:6px 0; padding-left:20px; }}
  a {{ color:#1d4ed8; }}
  .foot {{ color:var(--mut); font-size:12px; margin-top:28px; text-align:center; }}
</style></head>
<body><div class="wrap">
  <div class="top"><div class="logo">Cf</div>
    <div><h1>cardgen bridge</h1><p>CardFly Studio ↔ your Python card generator</p></div>
  </div>

  <div class="card"><h2>What this is</h2>
    <p><b>cardgen</b> is a small bridge between CardFly Studio (the browser app) and a Python
    function that generates a card. You put one decorator on your generator function and the
    bridge <b>reads the variables it needs</b> and serves them over HTTP so the editor can
    build its <i>Variables</i> tab automatically.</p>
  </div>

  <div class="card"><h2>Endpoints</h2>
    <table>
      <tr><th>Route</th><th>What you get</th></tr>
      <tr><td><span class="route">{ENDPOINT}</span></td><td>JSON contract of every card + its variables.</td></tr>
      <tr><td><span class="route">{DOCS_ENDPOINT}</span></td><td>This documentation page.</td></tr>
      <tr><td><span class="route">/</span></td><td>Mini index (JSON) listing both endpoints.</td></tr>
    </table>
  </div>

  <div class="card"><h2>Decorate your generator</h2>
    <p>The decorator inspects the function's signature — <b>names, defaults, type hints and
    <code>Field(...)</code> metadata</b> — and turns them into variables. A parameter without a
    default is <span class="pill">required</span>; one with a default is optional.</p>
    <pre><code>{escaped_snippet}</code></pre>
  </div>

  <div class="card"><h2>Run it</h2>
    <pre><code>python3 cardgen.py my_card.py            # auto-detects @card fns, serves on :{port}
python3 my_card.py                        # same, when it calls serve() in __main__</code></pre>
    <p>Then in CardFly Studio open the <b>Variables</b> tab → <b>Bridge → Listen</b>,
    point it at this address and the variables appear in the editor.</p>
  </div>

  <div class="card"><h2>Variable rules</h2>
    <table>
      <tr><th>Python</th><th>Becomes</th></tr>
      <tr><td><code>name: str</code></td><td>text variable</td></tr>
      <tr><td><code>photo: Image</code> / <code>= Image</code></td><td>image variable</td></tr>
      <tr><td><code>grade: Choice("A","B")</code></td><td>fixed-choice variable</td></tr>
      <tr><td><code>x: int = 5</code></td><td>text variable, optional, default <code>5</code></td></tr>
      <tr><td><code>id: Field(str, label="ID", required=True)</code></td><td>boxed metadata: label, help, default, required</td></tr>
    </table>
  </div>

  <div class="foot">CardFly Studio · cardgen bridge · served over HTTP on your machine.</div>
</div></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == DOCS_ENDPOINT or path == DOCS_ENDPOINT + "/":
            self._serve_html(_docs_html())
            return
        if path == ENDPOINT or path == ENDPOINT + "/":
            self._serve(200, {"ok": True, "contracts": _contracts()}, 133)
            return
        if path == "/" or path == "":
            self._serve(
                200,
                {
                    "ok": True,
                    "message": "cardgen HTTP bridge",
                    "contract_endpoint": ENDPOINT,
                    "docs_endpoint": DOCS_ENDPOINT,
                },
                111,
            )
            return
        self._serve(404, {"ok": False, "error": "not found"}, 99)

    def _serve(self, code, obj, _marker):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        if os.environ.get("CARDGEN_QUIET") != "1":
            super().log_message(*args)


def serve(port: Optional[int] = None, host: str = "127.0.0.1", daemon: bool = False):
    """Start the local HTTP listener that serves the contract to the app.

    If `daemon` is False (default) this blocks and runs until interrupted.
    """
    port = port or _pick_port()
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"\n  cardgen bridge ready  →  {url}{ENDPOINT}")
    print(f"  docs                →  {url}{DOCS_ENDPOINT}")
    print(f"  point CardFly Studio at:  {url}\n", flush=True)
    if daemon:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, t
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        server.server_close()


def _pick_port() -> int:
    env = os.environ.get("CARDGEN_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_PORT


# ---------------------------------------------------------------------------
# CLI: run one or more generator scripts then serve the contracts
# ---------------------------------------------------------------------------

def _main(argv):
    ap = argparse.ArgumentParser(
        prog="cardgen",
        description="Serve one or more CardFly card-generator scripts over HTTP "
                    "so the editor can read their variables automatically.",
    )
    ap.add_argument("scripts", nargs="*", help="Python file(s) containing @card generator functions")
    ap.add_argument("--port", type=int, default=None, help=f"HTTP port (default {DEFAULT_PORT})")
    ap.add_argument("--emit", metavar="FILE", default=None, help="also write contract JSON to FILE")
    ap.add_argument("--quiet", action="store_true", help="suppress request logging")
    args = ap.parse_args(argv)

    if args.quiet:
        os.environ["CARDGEN_QUIET"] = "1"

    if not args.scripts:
        _emit_any(args.emit, _contracts())
        serve(port=args.port)
        return

    for script in args.scripts:
        _load_script(script)

    contracts = _contracts()
    if not contracts:
        print("No @card generator functions found in the given scripts.", file=sys.stderr)
        sys.exit(1)

    _emit_any(args.emit, contracts)
    if not args.emit:
        serve(port=args.port)


def _emit_any(path, contracts):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(contracts, fh, ensure_ascii=False, indent=2)
    print(f"  wrote contract → {path}")


def _load_script(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("cardgen_user_script", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"Error importing {path}: {e}", file=sys.stderr)
        sys.exit(1)
    _LOADED_MODULES.append(mod)


def _all_wrappers():
    """Every @card-decorated function, from this module and any loaded script.

    Works whether the bridge runs as a script (`python cardgen.py x.py`) or is
    imported (`from cardgen import card`) — the decorated script may import the
    same module under a different name, so scan the loaded modules too.
    """
    seen = set()
    out = []
    for fn in list(_REGISTRY):
        if id(fn) not in seen:
            seen.add(id(fn))
            out.append(fn)
    for mod in _LOADED_MODULES:
        for val in vars(mod).values():
            if id(val) in seen:
                continue
            if callable(val) and getattr(val, "__cardgen_build__", None):
                seen.add(id(val))
                _REGISTRY.append(val)
                out.append(val)
    return out


def _contracts():
    wrappers = _all_wrappers()
    if _CONTRACT_CACHE and len(_CONTRACT_CACHE) == len(wrappers):
        return list(_CONTRACT_CACHE)
    build = []
    for fn in wrappers:
        fn_build = fn.__cardgen_build__
        if fn_build:
            c = fn_build()
            build.append(c)
    _CONTRACT_CACHE[:] = build
    return build


if __name__ == "__main__":
    _main(sys.argv[1:])
