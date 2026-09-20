<div align="center">

# Kaascan Card SDK

**Generate student ID cards from Python — single, batch, and web.**

Render ready-to-print PDF cards from Kaascan SVG templates with photos, QR codes,
barcodes, stamps, signatures and branding — secured by a QR-code API-user auth handshake.

[![Python](https://img.shields.io/badge/Python-3.10%2B-2a6faa?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/release-0.1.0-blue.svg)](card_agent_sdk-0.1.0-py3-none-any.whl)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20Windows-lightgrey)]()

</div>

---

## ✨ What it does

- 🪪 **Single cards** — one call renders a card and writes the final PDF.
- ⚡ **Batch generation** — fuse Kaascan admin data with a template and render hundreds of cards with process pooling.
- 🎨 **Branding assets** — stamps, signatures, barcodes, school logos, QR codes, custom colors and typography.
- 🗂️ **Template library** — git-style versioning for card designs (`build`, `commit`, `rollback`, `list`).
- 🌐 **Web layer** — FastAPI service for template previews and REST integrations.
- 🔐 **Secure API-user auth** — the admin API key is **never hard-coded**. A scannable QR code is printed in the terminal; your web service posts `{"admin_api_key": "..."}` to authorize the SDK, unlocking SINGLE / MULTIPLE card generation.

## 🖼️ Sample output

| Front | Back |
|-------|------|
| <img width="466" alt="card front" src="https://github.com/user-attachments/assets/3c5eac9b-7fbb-4370-91db-a3d2d750485b" /> | <img width="466" alt="card back" src="https://github.com/user-attachments/assets/a788e8e2-4f56-4252-8cbf-30e95050fb55" /> |

---

## 🚀 Quick start

```python
import asyncio

from card_sdk.card_agent import Card, Student

Student.photo    = "student.jpg"                     # or a base64 / https URL
Student.name     = "Tuyishimire Fraterine Ely"
Student.Class    = "Software Development"
Student.school_name = "KANYINYA TSS"
Student.student_id  = "S-2026-001"
Student.template_name = "BLUE GREEN STUDENT CARD"

student = Student(
    photo=Student.photo, name=Student.name, Class=Student.Class,
    school_name=Student.school_name, student_id=Student.student_id,
    template_name=Student.template_name,
)

asyncio.run(Card.agent(data=student))
```

The agent prints a **QR code** and waits for your web service to post the
`admin_api_key` before any card generation. Set `KAA_SCAN_API_KEY=<key>` in the
environment to skip the handshake for automation.

---

## 🔐 API-user authentication (no keys in code)

The SDK ships **without** an admin API key. On every run it:

1. Starts a tiny local endpoint (`POST /auth`, default port `8132`).
2. Prints a **scannable QR code** + the endpoint URL in the terminal.
3. Waits for your web service (WS) to post the key:

```bash
curl -X POST "http://<your-lan-ip>:8132/auth?token=<session-token>" \
  -H "Content-Type: application/json" \
  -d '{ "admin_api_key": "YOUR_ADMIN_API_KEY" }'
# → {"ok": true, "authorized": true, "message": "authorized"}
```

A tiny HTML authorize page (`GET /`) lets you paste the key from a phone.
The submitted key is **validated live** against the Kaascan admin API before
the SDK is authorized. It is kept **in memory only** and used for the
`X-API-KEY` header on every admin call.

| Variable             | Default   | Purpose                                   |
|----------------------|-----------|-------------------------------------------|
| `KAA_SCAN_API_KEY`   | *(unset)* | Env-supplied key — **skips the QR handshake** |
| `KAA_AUTH_HOST`      | `0.0.0.0` | Bind host for the auth endpoint           |
| `KAA_AUTH_PORT`      | `8132`    | Bind port (a free port is chosen if busy) |
| `KAA_AUTH_TIMEOUT`   | `180`     | Seconds to wait for the WS before giving up |

Programmatic control lives in `card_sdk/api_auth.py`:
`start_auth_server()`, `session_endpoint()`, `print_terminal_qrcode()`,
`await_authorization()`, `get/set_admin_api_key()`, `validate_admin_api_key()`.

---

## 📦 Install

```bash
# from the wheel in this repo
pip install card_agent_sdk-0.1.0-py3-none-any.whl

# or directly from GitHub
pip install "https://github.com/Jack-poker/card_sdk/raw/main/card_agent_sdk-0.1.0-py3-none-any.whl"
```

> The wheel is intentionally kept at `card_agent_sdk-0.1.0-py3-none-any.whl` so
> developer pipelines that auto-update from that exact filename keep working.

Requires **Python 3.10+**. Dependencies install automatically.

---

## 🛠️ Project layout

```
card_sdk/
├── api_auth.py               # QR-code API-user auth handshake + terminal QR
├── card_agent.py             # Card agent (SINGLE / MULTIPLE) + batch engine
├── card.py                   # generate_card(), save_card(), Student, CardConfig
├── base64qrcode.py           # styled QR / barcode generators (data-URI)
├── cardfly_server.py         # CardFly Studio binding (template editor)
├── web_server.py             # FastAPI web UI (template previews)
├── template_manager.py       # pull_template_options() / pull_template_files()
├── template_versioning.py    # git-style template design versions
├── cardgen/                  # CardFly card-generator bridge
├── fonts/                    # bundled card fonts (Minigap, FH Lecturis, …)
└── templates/templates_base/ # SVG + .card.kaascan card designs
```

---

## ⚡ Generating cards

### A single card

```python
from card_sdk.card import generate_card, image_file_to_base64

generate_card(
    template_name="BLUE GREEN STUDENT CARD",
    image_base64=image_file_to_base64("student.jpg"),
    student_name="Alice Johnson",
    student_class="Grade 4",
    school_name="SUNRISE ACADEMY",
    student_id="A-001",
    data_qrcode="https://kaascan.com/verify/A-001",
    # optional branding
    school_logo=image_file_to_base64("logo.png"),
    stamp_base64=image_file_to_base64("stamp.png"),
    student_barcode=image_file_to_base64("barcode.png"),
    signature_base64=image_file_to_base64("signature.png"),
)
```

Output lands in `output/<school_name>/<class>/final_student_card_<id>.pdf`.

### Structured config

```python
from card_sdk.card import CardConfig

cfg = CardConfig(
    photo=photo, template_name="BLUE GREEN STUDENT CARD",
    student_name="Tuyishimire Fraterine Ely", student_class="Software Development",
    school_name="KANYINYA TSS", student_id="S-2026-001",
    data_qrcode="https://kaascan.com/verify/S-2026-001",
)
content = cfg.generate_card_content()   # render + write PDF
student = cfg.to_student()              # build a Student for the batch agent
```

### Batch (multiple) mode

`Card.agent()` authenticates the API user, then asks for a school, template and
mode. In **MULTIPLE** mode it fetches students from the Kaascan admin API and
renders cards with process pooling (auto `os.cpu_count()` capped at 8).

---

## 🗂️ Template versioning

Card designs are SVG source files with git-style versions:

```bash
python3 -m card_sdk.template_versioning build BANK_INSPIRE
python3 -m card_sdk.template_versioning commit BANK_INSPIRE --label spring-2026 --message "Spring rebrand"
python3 -m card_sdk.template_versioning list
python3 -m card_sdk.template_versioning current BANK_INSPIRE
python3 -m card_sdk.template_versioning rollback BANK_INSPIRE --label v1
```

Placeholder data is filled through `{name}`, `{student_code}`, `{student_class}`,
`{school_name}`, `{student_id}`, `{student_photo}`, `{student_barcode}`,
`{valid_thru}`, `{school_type}`.

---

## 🌐 Web server & REST API

```bash
python -m card_sdk.web_server                  # port 8000
```

| Method | Endpoint                                | Purpose |
|--------|-----------------------------------------|---------|
| GET    | `/healthy/status`                       | Health check |
| GET    | `/card/templates`                       | List templates + preview paths |
| GET    | `/student/photo/<student_id>`           | Cached student photo (PNG) |
| GET    | `/student/card/<front\|back>/<id>`      | Rendered card side (SVG) |
| GET    | `/docs`                                 | Swagger UI |

---

## 📚 Documentation

See [`docs/INTEGRATION_GUIDE.html`](docs/INTEGRATION_GUIDE.html) for the full
integration guide: auth handshake, branding assets, REST API, template
versioning and the complete function reference.

## License

MIT — open source.