<div align="center">

# Kaascan Card SDK

**Student ID cards from Python — single, batch, and web.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-2a6faa?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

This README is a clean **integration guide** for developers. The full SDK source,
docs and examples live locally with the package — this repository hosts the
installable wheel.

---

## 1. Install

```bash
pip install "https://github.com/Jack-poker/card_sdk/raw/main/card_agent_sdk-0.1.0-py3-none-any.whl"
```

Python 3.10+. All dependencies resolve automatically.

## 2. Set your API user key (only needed for MULTIPLE mode)

**SINGLE card generation never needs a key** — it renders locally from your
template and student data. Only **MULTIPLE** (batch) mode pulls students from
the Kaascan admin API, so that is the path that requires the API user key.

For clean, non-interactive batch runs, set it from your environment — no QR
handshake needed:

```bash
export KAA_SCAN_API_KEY="<your-admin-api-key>"
```

When unset, MULTIPLE mode prints a QR code and awaits a `POST /auth` from your
web service before authorizing. See **§6** for the handshake.

## 3. Minimal integration — one card, one call

```python
from card_sdk.card import generate_card, image_file_to_base64

photo = image_file_to_base64("student.jpg")   # also: image_url_to_base64(url)

result = generate_card(
    template_name="BLUE GREEN STUDENT CARD",
    image_base64=photo,
    student_name="Tuyishimire Fraterine Ely",
    student_class="Software Development",
    school_name="KANYINYA TSS",
    student_id="S-2026-001",
    data_qrcode="https://kaascan.com/verify/S-2026-001",
)

print(result)   # "(completed output folder) : .../output/"
```

The final PDF is written to
`output/<school_name>/<student_class>/final_student_card_<student_id>.pdf`.
SINGLE generation requires **no API key**.

## 4. Structured integration — `CardConfig`

When you generate many cards with the same layout, keep your code declarative
with `CardConfig` (a full `pydantic` model):

```python
from card_sdk.card import CardConfig, image_url_to_base64

config = CardConfig(
    template_name="BLUE GREEN STUDENT CARD",
    photo=image_url_to_base64("https://api.kaascan.com/photos/s-2026-001.png"),
    student_name="Tuyishimire Fraterine Ely",
    student_class="Software Development",
    school_name="KANYINYA TSS",
    student_id="S-2026-001",
    student_code="5298 7601 2345 6789",        # barcode digits (auto-erased if wrong)
    valid_thru="09/30",
    data_qrcode="https://kaascan.com/verify/S-2026-001",
    # optional branding
    school_logo=image_url_to_base64("https://.../logo.png"),
    stamp=image_url_to_base64("https://.../stamp.png"),
    signature=image_url_to_base64("https://.../signature.png"),
    director_name="Mrs. Uwase",                # back-of-card school head
    director_contact="+250 78 000 0000",
)

content = config.generate_card_content()       # renders + writes the PDF
student = config.to_student()                  # reuse in the batch agent below
```

### Branding notes

Photos and logos are passed as **base64** strings. Helpers:
`image_file_to_base64(path)`, `image_url_to_base64(url)`,
`resize_image_base64(b64, max_width=100, max_height=100)`.

The barcode always carries the **digits** of `student_code` — if you supply a
malformed one it is regenerated automatically. A card carrying a barcode does
not need `data_qrcode` (and vice-versa).

## Where cards are written (output folder)

Final PDFs land at `output/<school_name>/<student_class>/final_student_card_<id>.pdf`,
with the **output root defaulting to the folder you are running the SDK from** —
an installed package never writes into `site-packages`.

Change it any of three ways:

```python
# 1) In code — absolute or relative (resolves against your working dir)
from card_sdk.card import set_output_dir, get_output_dir

set_output_dir("/srv/cards")          # PDFs → /srv/cards/<school>/<class>/...
set_output_dir("generated")           # → ./generated/<school>/<class>/...
set_output_dir(None)                  # reset to default ./output
print(get_output_dir())               # read the resolved path
```

```bash
# 2) Environment variable (also picked up by the batch worker processes)
export KAA_OUTPUT_DIR=/srv/cards
```

```bash
# 3) Default — just run from any folder; cards appear under ./output there
cd /my/project && python your_app.py
```

## CMYK colors for physical printing

Card PDFs are generated in **RGB** (screen colors), but print shops print in
**CMYK**. Enable the print option and every final card PDF is rewritten with
true 4-channel CMY+K colors (ICC SWOP profile, 4:4:4, 300 DPI by default), so
the printer reproduces the card design as-is instead of guessing an RGB→CMYK
conversion in the driver.

```python
# Off by default — flip it on for print runs
from card_sdk.card import set_print_cmyk, get_print_cmyk, convert_pdf_to_cmyk
from card_sdk.card_agent import Card

set_print_cmyk(True)
print(get_print_cmyk())            # True → every saved card PDF is CMYK

# ...or pass it straight to the agent for one run
asyncio.run(Card.agent(data=student, print_cmyk=True))
```

```bash
# ...or via the environment (also used by MULTIPLE-mode worker processes)
export KAA_PRINT_CMYK=true         # "true" | "1" | "yes" | "on"
export KAA_PRINT_CMYK_DPI=300      # rasterization resolution (default 300)
```

You can also convert an already-generated PDF manually:

```python
convert_pdf_to_cmyk("output/SCHOOL/CLASS/final_student_card_S-2026-001.pdf")
```

## Recolor the cards

Every template ships with its own colors, but you can recolor it per card
without touching the design. Colors that a template doesn't define are simply
ignored — so the safest overrides are `color` (primary), `side_2_color`
(back), `background_color` (card body) and `accent_color` (secondary accent).

```python
# Single card — recolor the built-in BLUE_TOPBAR_CARD
content = generate_card(
    template_name="BLUE_TOPBAR_CARD",
    image_base64=photo,
    student_name="ALINE UWASE",
    student_class="S1C",
    school_name="KIZIGURO SECONDARY",
    student_id="S-2026-001",
    color="#c00000",               # primary / banner bar (default: template)
    side_2_color="#004000",        # card back background
    background_color="#101010",    # card front body
)
```

SAME via the structured config or direct Student values:

```python
config = CardConfig(
    template_name="BANK_INSPIRE", student_name="ALINE UWASE", ...,
    color="#123456", accent_color="#9c27b0", side_2_color="#222222",
)
asyncio.run(Card.agent(data=config.to_student()))
```

Or batch, any hex you know the template uses:

```python
await multiple_cards("BANK_INSPIRE", data_url=..., color="#123456",
                     accent_color="#9c27b0", side_2_color="#222222",
                     color_overrides={"#999999": "#333333"})
```

For pixel-level control there is a generic override map — keys are either a
role name or the exact hex already on the template:

```python
await Card.agent(data=student)  # colors ride along on the Student object
student.color = "#c00000"
student.side_2_color = "#004000"
student.color_overrides = {"#28220b": "#111111"}   # literal hex swap
```

## 5. Batch generation — many cards (requires the API key)

`Card.agent()` then pulls the student list for a school from the Kaascan admin
API and renders cards with process pooling. Because it calls the admin API,
**MULTIPLE mode requires the API user key** (`KAA_SCAN_API_KEY` or the QR
handshake):

```python
import asyncio
from card_sdk.card_agent import Card

asyncio.run(Card.agent(data=config.to_student()))            # SINGLE → MULTIPLE prompt
asyncio.run(Card.agent(data=config.to_student(), print_cmyk=True))  # + CMYK print colors
# or interactive:  asyncio.run(Card.agent())
```

## 6. API-user auth handshake (MULTIPLE mode only)

The handshake runs **only when MULTIPLE mode is chosen and**
`KAA_SCAN_API_KEY` is not set. SINGLE mode never starts it.

1. The SDK starts a local server (`POST /auth`, default port `8132`) and prints a
   **QR code** + endpoint in the terminal.
2. Your web service posts the key:

```bash
curl -X POST "http://<lan-ip>:8132/auth?token=<session-token>" \
  -H "Content-Type: application/json" \
  -d '{ "admin_api_key": "YOUR_ADMIN_API_KEY" }'
# → {"ok": true, "authorized": true, "message": "authorized"}
```

The key is **validated live** against the Kaascan admin API, kept in memory only,
and used as the `X-API-KEY` header on every admin call.

| Env var            | Default   | Purpose                                    |
|--------------------|-----------|--------------------------------------------|
| `KAA_SCAN_API_KEY` | *(unset)* | MULTIPLE-mode key — skips the handshake (recommended for CI) |
| `KAA_AUTH_HOST`    | `0.0.0.0` | Auth endpoint bind host                    |
| `KAA_AUTH_PORT`    | `8132`    | Auth endpoint port (free port if busy)     |
| `KAA_AUTH_TIMEOUT` | `180`     | Seconds to wait for the WS before giving up |

---

## Repository layout

```
card_agent_sdk-0.1.0-py3-none-any.whl   ← installable wheel (stable filename)
```

## License

MIT.