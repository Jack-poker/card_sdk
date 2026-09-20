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

## 2. Set your API user key (so the SDK can talk to the Kaascan admin API)

The SDK ships **without** a hard-coded key. For non-interactive (clean) integration,
set it from your environment — no QR handshake needed:

```bash
export KAA_SCAN_API_KEY="<your-admin-api-key>"
```

When unset, the SDK prints a QR code and awaits a `POST /auth` from your web
service before authorizing card generation. See **§6** for the handshake.

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

## 5. Batch generation — many cards

`Card.agent()` authenticates the API user, then pulls the student list for a
school from the Kaascan admin API and renders cards with process pooling:

```python
import asyncio
from card_sdk.card_agent import Card

asyncio.run(Card.agent(data=config.to_student()))   # SINGLE → MULTIPLE prompt
# or interactive:  asyncio.run(Card.agent())
```

## 6. API-user auth handshake (when `KAA_SCAN_API_KEY` is not set)

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
| `KAA_SCAN_API_KEY` | *(unset)* | Skips the handshake — recommended for CI   |
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