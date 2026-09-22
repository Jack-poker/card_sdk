"""Resolve ``svg2pdf_py`` — bundled with the SDK, so it never has to come from PyPI.

``svg2pdf-py==1.1.0`` ships platform wheels only (no source distribution), which
makes plain pip resolution fail on unsupported systems. To keep the SDK
self-contained, the wheels are bundled inside the package (``card_sdk/vendored``)
and installed from there — offline, against the user's own interpreter — the
first time the renderer is needed.
"""

import os
import subprocess
import sys


def _bundled_wheels():
    vendored = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendored")
    if not os.path.isdir(vendored):
        return []
    return sorted(os.path.join(vendored, f) for f in os.listdir(vendored) if f.endswith(".whl"))


def _install_bundled(pip_args):
    wheels = _bundled_wheels()
    if not wheels:
        return False
    cmd = [sys.executable, "-m", "pip", "install", *pip_args, "--no-index", "--find-links", os.path.dirname(wheels[0])]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _import_svg2pdf_py():
    """Import the SVG renderer, auto-installing the bundled wheel if missing."""
    try:
        import svg2pdf_py  # noqa: F401

        return svg2pdf_py
    except ImportError:
        # 1) local bundled wheel first (two tries: newest abi first, then broad)
        for extra in (["--prefer-binary", "--upgrade"], []):
            if _install_bundled(extra):
                try:
                    import svg2pdf_py  # noqa: F401

                    return svg2pdf_py
                except ImportError:
                    continue
        # 2) last resort: normal PyPI resolution (network required)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--prefer-binary", "svg2pdf-py==1.1.0"],
            capture_output=True,
            text=True,
        )
        import svg2pdf_py  # noqa: F401

        return svg2pdf_py


svg2pdf_py = _import_svg2pdf_py()