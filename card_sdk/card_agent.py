import json
import os
import pathlib

from typing import Dict, Optional, Any

import ui
from card_sdk.terminal_kid import Tkd
from card_sdk.card import (
    clear_report_file,
    base_dir,
    get_output_dir,
    single_image_url_to_base64,
    generate_card,
    Student,
    CardConfig,
    save_card,
    async_image_url_to_base64,
    image_file_to_base64,
    base64_qrcode,
    check_image_cache,
    _ensure_barcode_image,
    build_card_fmt_args,
    render_card_svgs,
    _sanitize_font_families,
    report_missing,
    get_print_cmyk,
    set_print_cmyk,
    convert_pdf_to_cmyk,
)
import asyncio
import multiprocessing
import time
import aiohttp
import subprocess
from concurrent.futures import ProcessPoolExecutor
import requests
import requests_cache
from color_cli import color_text
from super_progress_bar import Progress
from jload import jsave
import shlex
from dotenv import load_dotenv
from card_sdk.template_manager import pull_template_options
from card_sdk.api_auth import (
    get_admin_api_key,
    set_admin_api_key,
    require_admin_api_key,
)

# Legacy hard-coded admin API key — REMOVED. The admin API key is now supplied
# at runtime through the QR-code authorization handshake (see api_auth.py) or
# the KAA_SCAN_API_KEY environment variable.

# Load environment variables
load_dotenv()


enable_image_caching = os.getenv("ENABLE_IMAGE_CACHING")

prg = 0

progress = Progress(
    min_value=0,
    max_value=100,
    unit="items",
    colors=[(0, 0, 0), (255, 255, 0), (255, 255, 0), (0, 0, 0)],
    single_color=False,
)


# Student.photo = image_url_to_base64("https://admin.kaascan.com/assets/3a5e8e31-2a75-41b8-82ee-868127cb7562?download=")
# Student.name = "Fraterine Ely"
# Student.Class = "Software Devlopment"
# Student.school_name = "Lyce de nyanza"
# Student.data_qrcode = base64_qrcode({"name":"emmanuelzzzzzzzz"})


async def total_users(data_url="https://api.v2.kaascan.com/admin/students") -> dict:
    api_key = get_admin_api_key()
    if not (api_key and api_key.isascii() and "\n" not in api_key and "\r" not in api_key):
        print("[-] admin_api_key is missing or not plain ASCII — re-authorize first.")
        return {}
    try:
        # Get user data
        response = requests.get(
            data_url,
            headers={
                "accept": "application/json",
                "X-API-KEY": api_key,
            },
            timeout=(10, 60),
        )

        total_users = response.json()["count"]

        jsave({"total_users": total_users}, file_path=f"{base_dir}/report/users.json")

        return total_users

    except Exception as error:
        print(f"[-] something went wrong: {error}")


async def get_csrftoken() -> str:
    try:
        fetch_csrftoken = requests.get(
            "https://api.v2.kaascan.com/get-csrf-token", timeout=(10, 30)
        ).json()

        csrf_token = fetch_csrftoken["csrf_token"]

        return csrf_token

    except Exception as error:
        print(f"[-] fetching csrf token failed: {error}")


async def fetch_schools() -> list:
    try:

        fnd_schools = []
        fetch_schools = requests.get(
            "https://automation.kaascan.com/webhook/schools", timeout=(10, 30)
        )

        schools = fetch_schools.json()[0]

        for school_name in schools:
            fnd_schools.append(schools[school_name])

        return fnd_schools

    except Exception as error:
        print(f"[-] Fetching schools failed: {error}")
        return fnd_schools


def _prompt_choice(text: str, options: list):
    """Pick from *options* via the CLI, defaulting to the first on no TTY/EOF."""
    if not options:
        return ""
    try:
        return ui.ask_choice(text, options)
    except (EOFError, KeyboardInterrupt, AttributeError):
        return options[0]


def _prompt_yes_no(text: str) -> bool:
    try:
        return ui.ask_yes_no(text)
    except (EOFError, KeyboardInterrupt):
        return False


# students
_PHOTO_CONCURRENCY = int(os.getenv("KAA_SCAN_PHOTO_CONCURRENCY", "32"))


async def _fetch_students_metadata(data_url: str) -> tuple:
    """Fetch the student JSON records + total count (one bounded request)."""
    api_key = get_admin_api_key()
    if not (api_key and api_key.isascii() and "\n" not in api_key and "\r" not in api_key):
        print("[*] admin_api_key is missing or not plain ASCII — FETCHING USER DATA FAILED. Re-authorize first.")
        return [], 0

    response = requests.get(
        data_url,
        headers={
            "accept": "application/json",
            "X-API-KEY": api_key,
        },
        timeout=(10, 60),
    )
    payload = response.json()
    users = payload.get("data") or []
    return users, payload.get("count") or len(users)


async def fetch_userdata_stream(users: list) -> Any:
    """Download every student photo and yield each processed record the
    moment its photo is ready (bounded concurrency).

    multiple_cards() consumes this as an async iterator, so card rendering can
    start on a finished photo while the rest of the batch is still downloading
    — the CPU-bound render phase overlaps the I/O-bound download phase instead
    of running strictly after it.
    """
    if not users:
        return

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    sem = asyncio.Semaphore(max(1, min(_PHOTO_CONCURRENCY, len(users))))

    async with aiohttp.ClientSession(connector=connector) as session:

        async def _one(data, inc):
            async with sem:
                return await process_student(session, data, inc)

        for future in asyncio.as_completed(
            [_one(data, inc) for inc, data in enumerate(users, start=1)]
        ):
            try:
                record = await future
            except Exception as exc:
                print(f"[*] student fetch failed: {exc}")
                continue
            if record and "student_photo" in record:
                yield record


async def fetch_userdata(data_url: str) -> list:
    """Download all photos for *data_url* and return the processed records."""
    users, _ = await _fetch_students_metadata(data_url)
    return [record async for record in fetch_userdata_stream(users)]


def create_classFolder(school_folder_name: str, folder_name: str):
    class_dir = get_output_dir() / school_folder_name / folder_name
    class_dir.mkdir(parents=True, exist_ok=True)


async def extract_base64_image(session, data, count: int):

    try:

        pull_image_cache = await check_image_cache(user_id=data["student_id"])
        pull_status = pull_image_cache["status"]

        if pull_status and enable_image_caching == "enabled":

            Tkd.inform_user(
                f"pulling cache: {color_text(text=data["student_id"][:6],color="green")}"
            )

            Tkd.hello()

            print(color_text(text="(cache pulling) Please wait...", color="blue"))

            return pull_image_cache["base64_image"]
        else:
            base64_image_from_url = await async_image_url_to_base64(
                session, data["student_photo_url"], data["student_id"], count
            )
            return base64_image_from_url

    except Exception as error:
        print(error)


async def process_student(session, data, count: int):
    """Create student class folder"""
    create_classFolder(
        school_folder_name=data["school_name"], folder_name=data["grade"]
    )
    """Process single student"""

    # os.system("clear")
    # print(progress.update(prg))

    return {
        "student_photo": await extract_base64_image(session, data, count),
        "student_name": data["student_name"],
        "student_class": data["grade"],
        "school_name": data["school_name"],
        # Respect an explicit QR payload from the source; otherwise leave it
        # empty so a barcode-based card does not get a spurious QR (the batch
        # flow decides which to render).
        "data_qrcode": base64_qrcode(data["data_qrcode"]) if data.get("data_qrcode") else "",
        "student_id": data["student_code"],
        # The admin API does not always carry the card/school fields. Copy the
        # ones it does send through per student — multiple_cards() falls back to
        # the run-wide values from the Student when a record leaves one out, so
        # dropping them here would silently discard real per-student data.
        "student_code": str(data.get("student_code") or data.get("code") or ""),
        "valid_thru": str(data.get("valid_thru") or ""),
        "school_type": str(data.get("school_type") or ""),
        "slogan": str(data.get("slogan") or data.get("school_slogan") or ""),
        "director_name": str(data.get("director_name") or ""),
        "director_contact": str(data.get("director_contact") or ""),
    }


async def single_extract_base64_image(data):

    try:

        pull_image_cache = await check_image_cache(user_id=data.student_id)
        pull_status = pull_image_cache["status"]

        if pull_status and enable_image_caching == "enabled":

            Tkd.inform_user(
                f"pulling cache: {color_text(text=data["student_id"][:6],color="green")}"
            )

            Tkd.hello()

            print(color_text(text="(cache pulling) Please wait...", color="blue"))

            return pull_image_cache["base64_image"]
        else:
            base64_image_from_url = await single_image_url_to_base64(
                data.photo, data.student_id
            )
            return base64_image_from_url

    except Exception as error:
        print(error)


async def single_card(data: Student, stamp_base64: str = "",
                      signature_base64: str = "",
                      barcode_base64: str = "",
                      school_logo_base64: str = "") -> str:

    # A card that carries a barcode does not need a QR: leave data_qrcode as an
    # empty string / empty dict ("{}") to suppress the QR entirely.
    qr_uri = ""
    if data.data_qrcode and str(data.data_qrcode).strip() and str(data.data_qrcode).strip() != "{}":
        qr_uri = base64_qrcode(data.data_qrcode)

    result = generate_card(
        template_name= data.template_name,
        image_base64=await single_extract_base64_image(data),
        student_name=data.name,
        student_class=data.Class,
        data_qrcode=qr_uri,
        school_name=data.school_name,
        student_id=data.student_id,
        student_code=getattr(data, "student_code", "") or "",
        valid_thru=getattr(data, "valid_thru", "") or "09/30",
        school_type=getattr(data, "school_type", "") or "HIGH SCHOOL",
        stamp_base64=stamp_base64 or getattr(data, "stamp", None) or "",
        student_barcode=barcode_base64 or getattr(data, "barcode", None) or "",
        signature_base64=signature_base64 or getattr(data, "signature", None) or "",
        school_logo=school_logo_base64 or getattr(data, "school_logo", None) or "",
        school_slogan=getattr(data, "slogan", "") or "",
        director_name=getattr(data, "director_name", "") or "",
        director_contact=getattr(data, "director_contact", "") or "",
        school_name_font_size=getattr(data, "school_name_font_size", 0) or 0,
        school_name_font_size_front=getattr(data, "school_name_font_size_front", None),
        school_name_font_size_back=getattr(data, "school_name_font_size_back", None),
        student_name_font_size=getattr(data, "student_name_font_size", 0) or 0,
        student_name_font_size_front=getattr(data, "student_name_font_size_front", None),
        student_name_font_size_back=getattr(data, "student_name_font_size_back", None),
        student_class_font_size=getattr(data, "student_class_font_size", 0) or 0,
        student_class_font_size_front=getattr(data, "student_class_font_size_front", None),
        student_class_font_size_back=getattr(data, "student_class_font_size_back", None),
        side_2_color=getattr(data, "side_2_color", "") or "",
        color=getattr(data, "color", None),
        background_color=getattr(data, "background_color", None),
        accent_color=getattr(data, "accent_color", None),
        color_overrides=getattr(data, "color_overrides", None),
        colors=getattr(data, "colors", None),
    )

    Tkd.checking_task_progress(result, 80)
    print(color_text("[-] Single card generated successful. \n \n", "light_green"))
    print(
        color_text(
            f"[-] Check output folder: {get_output_dir()}/{data.school_name}/{data.Class}/ \n \n",
            "light_green",
        )
    )

    return result


def count_cards():
    from card_sdk.card import current_report_count

    return current_report_count()


def _render_card_worker(args: dict) -> dict:
    """Render one card in a fresh worker process.

    Creates its own font database inside the process (native state is never
    inherited across a fork/spawn — the old joblib path forked a process whose
    global font db could silently fail in svg2pdf) and renders straight from
    in-memory SVG strings, exactly like save_card().
    """
    import pymupdf
    from card_sdk._svg2pdf import svg2pdf_py

    student_id = args.get("student_id", "card") or "card"
    # Cards are named by the *student_code* (the card number); fall back to
    # student_id only when no code was supplied.
    card_code = str(args.get("student_code") or "").strip() or student_id
    try:
        font_db = svg2pdf_py.FontDatabase()
        font_db.load_font_file(f"{base_dir}/fonts/minigap.otf")
        font_db.load_font_file(f"{base_dir}/fonts/FHLecturis-Bold.ttf")
        # Gilroy fonts used by BLUE_TOPBAR_CARD template
        for _gilroy in ("Gilroy-UltraBold.ttf", "Gilroy-Light.ttf"):
            try:
                font_db.load_font_file(f"{base_dir}/fonts/{_gilroy}")
            except Exception:
                pass
        # Credit Card font used by BANK_INSPIRE for card numbers / student IDs
        try:
            font_db.load_font_file(f"{base_dir}/fonts/CreditCard-26Me.ttf")
        except Exception:
            pass
        # Space Grotesk used by BANK_INSPIRE (Iyi paragraph, phone, support line).
        # Without it those <text> elements are silently dropped, because
        # _loaded_font_names already whitelists the family so the sanitizer does
        # not fall back to another face.
        try:
            font_db.load_font_file(f"{base_dir}/fonts/SpaceGrotesk.otf")
        except Exception:
            pass
        # Z003 used by RUNO SCHOOL CARD. Without it that <text> element is
        # silently dropped, because _loaded_font_names whitelists the family so
        # the sanitizer does not fall back to another face. Both bundled files
        # are the same cut, so first one that loads wins.
        for _z003 in ("Z003-MediumItalic.otf", "Z003-MediumItalic.ttf"):
            try:
                font_db.load_font_file(f"{base_dir}/fonts/{_z003}")
                break
            except Exception:
                continue
    except Exception:
        font_db = svg2pdf_py.FontDatabase()

    try:
        student_barcode = _ensure_barcode_image(
            args.get("student_code", ""), args.get("student_barcode", "")
        )
        fmt_args = build_card_fmt_args(
            student_name=args["student_name"],
            student_class=args["student_class"],
            school_name=args["school_name"],
            student_id=student_id,
            student_code=args.get("student_code", ""),
            valid_thru=args.get("valid_thru", ""),
            school_type=args.get("school_type", ""),
            school_slogan=args.get("school_slogan", ""),
            school_logo=args.get("school_logo", ""),
            image_base64=args.get("image_base64", ""),
            side_2_color=args.get("side_2_color", ""),
            data_qrcode=args.get("data_qrcode", ""),
            stamp_base64=args.get("stamp_base64", ""),
            student_barcode=student_barcode,
            signature_base64=args.get("signature_base64", ""),
            director_name=args.get("director_name", ""),
            director_contact=args.get("director_contact", ""),
            school_name_font_size=args.get("school_name_font_size", 0) or 0,
            school_name_font_size_front=args.get("school_name_font_size_front", None),
            school_name_font_size_back=args.get("school_name_font_size_back", None),
            student_name_font_size=args.get("student_name_font_size", 0) or 0,
            student_name_font_size_front=args.get("student_name_font_size_front", None),
            student_name_font_size_back=args.get("student_name_font_size_back", None),
            student_class_font_size=args.get("student_class_font_size", 0) or 0,
            student_class_font_size_front=args.get("student_class_font_size_front", None),
            student_class_font_size_back=args.get("student_class_font_size_back", None),
        )
        faces = render_card_svgs(args["template_name"], fmt_args)
        front, back = faces["front"], faces["back"]
    except Exception as exc:
        return {"student_id": card_code, "ok": False, "error": f"fill: {exc}"}

    # Per-role color overrides, applied identically to generate_card(): back side
    # follows side_2_color, other roles (primary/background/accent) cover both.
    from card_sdk.card import _resolve_color_overrides, _apply_color_overrides, _TEMPLATE_COLOR_ROLES

    overrides = _resolve_color_overrides(
        args.get("template_name", ""),
        color=args.get("color"),
        background_color=args.get("background_color"),
        accent_color=args.get("accent_color"),
        color_overrides=args.get("color_overrides"),
        colors=args.get("colors"),
    )
    back_overrides = dict(overrides)
    back_roles = _TEMPLATE_COLOR_ROLES.get(args["template_name"], {})
    if back_roles.get("background"):
        back_overrides.pop(back_roles["background"], None)
    if args.get("side_2_color") and back_roles.get("back_bg"):
        back_overrides[back_roles["back_bg"]] = str(args["side_2_color"])
    front = _apply_color_overrides(front, overrides)
    back = _apply_color_overrides(back, back_overrides)

    # Report any required element that wasn't supplied so it doesn't silently
    # vanish from the card (stamp / signature / barcode / logo / QR / photo).
    # ``data_qrcode`` is an alternative to ``student_barcode``: a card that
    # carries a barcode does not need QR data, so skip that check when a
    # barcode is present.
    try:
        _has_barcode = bool(student_barcode and str(student_barcode).strip())
        _missing = [
            name
            for name, raw in (
                ("stamp_base64", args.get("stamp_base64")),
                ("signature_base64", args.get("signature_base64")),
                ("student_barcode", student_barcode),
                ("school_logo", args.get("school_logo")),
                ("data_qrcode", args.get("data_qrcode")),
                ("image_base64", args.get("image_base64")),
            )
            if not (raw and str(raw).strip())
            and not (name == "data_qrcode" and _has_barcode)
        ]
        if _missing:
            report_missing(
                card_id=card_code,
                school_name=args.get("school_name", ""),
                student_class=args.get("student_class", ""),
                missing=_missing,
            )
    except Exception:
        pass

    out_dir = (
        pathlib.Path(args.get("output_dir") or get_output_dir())
        / args["school_name"]
        / args["student_class"]
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"final_student_card_{card_code}.pdf"

    try:
        front = _sanitize_font_families(front)
        back = _sanitize_font_families(back)
        pdf_pages = svg2pdf_py.svg_pages_to_pdfs([front, back], font_db)
        if not pdf_pages:
            print(f":: warning: svg-to-pdf produced no pages for {card_code}")
        doc = pymupdf.open()
        for pb in pdf_pages:
            part = pymupdf.open("pdf", pb)
            doc.insert_pdf(part)
            part.close()
        doc.save(str(out_pdf), garbage=0, deflate=False)
        doc.close()
        if args.get("print_cmyk", get_print_cmyk()):
            convert_pdf_to_cmyk(str(out_pdf))
    except Exception as exc:
        return {"student_id": card_code, "ok": False, "error": f"render: {exc}", "out": str(out_pdf)}
    return {"student_id": card_code, "ok": True, "out": str(out_pdf)}


_RENDER_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_RENDER_QUIPS = (
    "summoning the card designer...",
    "haggling with the printer daemon...",
    "convincing svg2pdf that ink is not lazy...",
    "chasing stray pixels back into place...",
    "reminding the barcode to keep stock-still...",
    "polishing the laminate shine...",
    "waking up the document scanners...",
    "measuring widths in millimeters...",
    "signing autographs on the backface...",
    "double-checking nobody moved a textbox...",
)


async def _drive_render(
    futures: set, results: list, total: int, producer_done: asyncio.Event
) -> None:
    """Animated realtime card-render progress.

    One self-updating console line: spinner + rotating quip + progress bar. The
    producer keeps adding render futures to *futures* as photos finish
    downloading; once *producer_done* is set and every submitted future has
    finished, the loop ends.
    """
    if total <= 0:
        return
    spin = _RENDER_SPINNER
    quips = _RENDER_QUIPS
    done = 0
    i = 0
    while True:
        waiting = {f for f in futures if not f.done()}
        if waiting:
            _finished, _ = await asyncio.wait(
                waiting, timeout=0.12, return_when=asyncio.FIRST_COMPLETED
            )
        else:
            _finished = set()
            await asyncio.wait(
                {asyncio.ensure_future(producer_done.wait())}, timeout=0.12
            )
        for fut in _finished:
            try:
                results.append(await fut)
            except Exception as exc:
                results.append({"student_id": "?", "ok": False, "error": str(exc)})
            done += 1
        i += 1
        frame = spin[i % len(spin)]
        quip = quips[(i // len(spin)) % len(quips)]
        filled = int(round(36 * done / max(1, total)))
        filled = max(0, min(36, filled))
        bar = "█" * filled + "░" * (36 - filled)
        phase = (
            "Finalizing"
            if producer_done.is_set()
            else "Downloading & rendering"
        )
        print(
            f"\r{frame} {phase} ... {done}/{total} [{bar}] {quip}",
            end="",
            flush=True,
        )
        if producer_done.is_set() and all(f.done() for f in futures):
            print()
            return


async def multiple_cards(
    template_name: str, data_url="https://api.v2.kaascan.com/admin/students",
    stamp_base64: str = "", signature_base64: str = "", barcode_base64: str = "", school_logo_base64: str = "",
    color=None, side_2_color="#00897B", background_color=None, accent_color=None, color_overrides=None, colors=None,
    student_code: str = "", valid_thru: str = "09/30", school_type: str = "HIGH SCHOOL",
    school_slogan: str = "", director_name: str = "", director_contact: str = "",
    school_name_font_size: float = 0.0,  # mm +/- school-name font delta (0 = authored)
    school_name_font_size_front=None, school_name_font_size_back=None,
    student_name_font_size: float = 0.0,  # mm +/- student-name font delta (0 = authored)
    student_name_font_size_front=None, student_name_font_size_back=None,
    student_class_font_size: float = 0.0,  # mm +/- grade font delta (0 = authored)
    student_class_font_size_front=None, student_class_font_size_back=None,
):
    # Metadata fetch is a single bounded request; photos download and cards
    # render as an overlapped pipeline, so the CPU-bound render phase hides
    # behind the I/O-bound download phase.
    users, total = await _fetch_students_metadata(data_url)
    if not users:
        return []

    # A card that carries a barcode does not need a QR — drop any QR data that
    # was pre-generated so the QR placeholder stays empty on barcode cards.
    suppress_qr = bool(barcode_base64 and str(barcode_base64).strip())

    print(
        color_text(
            f"[>] Downloading & rendering {len(users)} student card(s) "
            "(bounded photo downloads, overlapped with CPU render) ...",
            "cyan",
        ),
        flush=True,
    )

    def build_render_task(record: dict, student_id: str) -> dict:
        """A per-student record value wins; the run-wide value fills the gap."""
        def _pick(key, fallback):
            value = str(record.get(key) or "").strip()
            return value or fallback

        return {
            "template_name": template_name,
            "output_dir": str(get_output_dir()),
            "print_cmyk": bool(get_print_cmyk()),
            "color": color,
            "side_2_color": side_2_color,
            "background_color": background_color,
            "accent_color": accent_color,
            "color_overrides": color_overrides,
            "colors": colors,
            "image_base64": record["student_photo"],
            "student_name": record.get("student_name", ""),
            "student_class": record.get("student_class", ""),
            "school_name": record.get("school_name", ""),
            "student_id": student_id,
            "data_qrcode": "" if suppress_qr else record.get("data_qrcode", ""),
            "stamp_base64": stamp_base64,
            "student_barcode": barcode_base64,
            "signature_base64": signature_base64,
            "school_logo": school_logo_base64,
            "student_code": _pick("student_code", student_code),
            "valid_thru": _pick("valid_thru", valid_thru),
            "school_type": _pick("school_type", school_type),
            "school_slogan": _pick("slogan", school_slogan),
            "director_name": _pick("director_name", director_name),
            "director_contact": _pick("director_contact", director_contact),
            "school_name_font_size": school_name_font_size,
            "school_name_font_size_front": school_name_font_size_front,
            "school_name_font_size_back": school_name_font_size_back,
            "student_name_font_size": student_name_font_size,
            "student_name_font_size_front": student_name_font_size_front,
            "student_name_font_size_back": student_name_font_size_back,
            "student_class_font_size": student_class_font_size,
            "student_class_font_size_front": student_class_font_size_front,
            "student_class_font_size_back": student_class_font_size_back,
        }

    workers = max(
        1, min(int(os.getenv("KAA_SCAN_MAX_WORKERS", "8")), os.cpu_count() or 1)
    )
    # Spawn, never fork: the parent's event loop already owns aiohttp threads
    # (ThreadedResolver) by this point, and forking a process that has live
    # threads inherits locked mutexes — a child renderer then deadlocks forever
    # and the batch freezes at the last printed line.
    _mp_ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=_mp_ctx) as pool:
        loop = asyncio.get_running_loop()
        pending: set = set()
        results: list = []
        producer_done = asyncio.Event()

        # Submit every render the instant its photo is ready (NOT pool.map,
        # which yields in input order and stalls the display on the slowest
        # prefix). _drive_render owns a single animated line.
        async def _produce():
            try:
                async for record in fetch_userdata_stream(users):
                    if not record or "student_photo" not in record:
                        continue
                    student_id = record.get("student_id", "")
                    pending.add(
                        loop.run_in_executor(
                            pool,
                            _render_card_worker,
                            build_render_task(record, student_id),
                        )
                    )
            finally:
                producer_done.set()

        producer_task = asyncio.create_task(_produce())
        await _drive_render(pending, results, total, producer_done)
        await producer_task

    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    for r in ok:
        Tkd.inform_user(r["student_id"])
    for r in failed:
        Tkd.inform_user(f"failed {r['student_id']}: {r.get('error')}")
    return ok


def create_output_Folder():
    get_output_dir().mkdir(parents=True, exist_ok=True)


class Card:

    @staticmethod
    async def authorize_api_user():
        """QR-code handshake: wait for the WS to POST a valid ``admin_api_key``.

        Starts the local auth endpoint, prints a scannable QR + the endpoint
        URL in the terminal, and blocks until the admin's web service posts
        ``{"admin_api_key": "..."}``. The received key authorizes the SDK and
        unlocks MULTIPLE (batch) card generation. SINGLE mode does not touch
        the Kaascan admin API, so it never requires a key.
        """
        from card_sdk import api_auth

        # An env-supplied key (KAA_SCAN_API_KEY) is a legitimate alternative —
        # the runtime handshake is only needed when no key is pre-configured.
        if api_auth.require_admin_api_key():
            return get_admin_api_key()

        server, session = api_auth.start_auth_server()

        try:
            endpoint = api_auth.session_endpoint(server)

            print(color_text("\n\n  [AUTH] Authorize the API user", "green"))
            print(
                color_text(
                    "  Scan the QR code (or open the endpoint) and post the\n"
                    "  'admin_api_key' your web service will send back.\n",
                    "white",
                )
            )
            print(color_text(f"  Endpoint -> {endpoint}\n", "yellow"))
            print(
                color_text(
                    f"  Waiting {api_auth.AUTH_TIMEOUT}s for authorization ... (Ctrl+C to abort)\n",
                    "blue",
                )
            )
            api_auth.print_terminal_qrcode(endpoint)

            api_key = await api_auth.await_authorization(
                session, timeout=api_auth.AUTH_TIMEOUT
            )
        finally:
            server.shutdown()

        if not api_key:
            raise RuntimeError(
                "Authorization failed — no admin_api_key was received. "
                "The web service must POST {\"admin_api_key\": \"...\"} to the endpoint."
            )

        set_admin_api_key(api_key)
        print(color_text("\n  [✓] API user authorized — card generation unlocked.\n", "green"))
        return api_key

    @staticmethod
    async def agent(
        data: Optional[Student] = None,
        print_cmyk: Optional[bool] = None,
        multiple: bool = False,
        template_name: Optional[str] = None,
    ):
        """Generate one card, or a whole batch.

        Args:
            data: the student to render. Optional — only needed to carry the
                template and the colour choices into MULTIPLE mode, where the
                students themselves come from the Kaascan admin API.
            print_cmyk: per-run CMYK toggle. ``None`` leaves the current
                setting (and ``KAA_PRINT_CMYK``) alone.
            multiple: opt into MULTIPLE (batch) generation without going
                through the interactive menu. The default ``False`` keeps the
                historical behaviour: with ``data`` given, render exactly one
                card; without it, show the CLI cockpit.
            template_name: template for batch generation. Falls back to
                ``data.template_name``, then to the interactive picker. An
                explicit value always wins, so a batch run never blocks on a
                prompt. Has no effect in SINGLE mode, which always uses
                ``data.template_name``.

        Returns:
            SINGLE mode: whatever ``single_card`` returns. MULTIPLE mode: the
            list of successful worker results.

        MULTIPLE mode reads the admin API, so it authorizes the API user first
        (a no-op when ``KAA_SCAN_API_KEY`` is already set). SINGLE mode never
        touches the admin API and needs no key.
        """
        # create output folder
        create_output_Folder()

        # Per-run CMYK print toggle — mirrors set_print_cmyk()/KAA_PRINT_CMYK and
        # is propagated to MULTIPLE-mode worker processes via the environment.
        if print_cmyk is not None:
            os.environ["KAA_PRINT_CMYK"] = "true" if print_cmyk else "false"
            set_print_cmyk(print_cmyk)
        # make template update — best-effort only: generation always works from
        # the bundled templates, so a blocked webhook must never kill a notebook.
        try:
            from card_sdk.update import update_template
        except Exception as _imp_error:

            print(
                color_text(
                    f"[-] Template update module unavailable ({_imp_error.__class__.__name__}: {_imp_error}) — using bundled templates.",
                    "yellow",
                )
            )
            update_template = None

        try:
            print(color_text("\n \n [>] υρᑯα𝗍𝗂𐓣𝗀‌ \n \n", "green"))
            if update_template is not None:
                await update_template()
                print("[*] updating Finished [1]")
            else:
                print("[*] updating skipped (module unavailable)")
        except Exception as error:
            print(f"[-] Template update skipped (using bundled templates): {error}")

        Tkd.cleaner()

        Tkd.hello()

        # A caller that asked for MULTIPLE has already chosen the mode, so the
        # interactive menu is skipped entirely.
        batch = bool(multiple)

        # Data is already in hand and SINGLE was not overridden — nothing to ask,
        # go straight to SINGLE-page generation. Interactive menus are only used
        # for the no-data CLI cockpit.


        if data is not None and not batch:
            clear_report_file()
            return await single_card(data)

        mode: str = "MULTIPLE" if batch else ""
        template: str = template_name or ""



        if not batch:
            # Optional: start the web UI so templates can be viewed over HTTP.
            if _prompt_yes_no(color_text("Start the web server to view templates?", "blue")):
                Tkd.inform_user("Starting web server ... (press Ctrl+C to stop)")
                from card_sdk.web_server import serve_web_async

                await serve_web_async()
                return

            try:
                modes = ["SINGLE", "MULTIPLE"]
                mode: str = _prompt_choice("Choose generating mode: ", modes)
            except (EOFError, KeyboardInterrupt, AttributeError):
                mode = "SINGLE"

        # School + template pickers intentionally appear *after* authentication
        # (in the MULTIPLE branch below), so a freshly authorized session offers
        # the real choices.

        # Clear the report file
        clear_report_file()
        


        try:

            Tkd.hello()

            if mode.lower() == "single" and data != None:

                singleCard_result = await single_card(data)
                return singleCard_result

            if mode.lower() == "multiple":
                # MULTIPLE mode pulls students from the Kaascan admin API, so
                # the API user key is required here — SINGLE mode never touches
                # the admin API and needs no key. Authenticate first; then the
                # interactive steps (choose school, choose template) appear.
                await Card.authorize_api_user()

                # Interactive steps appear right after authenticating, so the
                # freshly authorized session offers the real choices. Batch runs
                # accept the template carried by `data` without blocking on a
                # prompt; interactive runs get the pickers.
                if not batch:
                    try:
                        schools = await fetch_schools()
                        school = _prompt_choice("Choose school: ", schools)
                    except (EOFError, KeyboardInterrupt, AttributeError):
                        school = ""
                if not template:
                    template = (getattr(data, "template_name", None) or "") if batch else ""
                    if not template:
                        template = _prompt_choice("Choose Template", pull_template_options())

                # get total users number
                await total_users()
                # The per-run assets live on the Student, exactly as in
                # single_card, so a batch run uses the same stamp/signature/
                # barcode/logo as the single-card path. Explicit arguments win.
                # The school-level card fields (card number, validity, school
                # type, slogan, director) come along too: the admin API only
                # supplies name/class/photo/id, so without these a batch card
                # fell back to the template's demo text.
                return await multiple_cards(
                    template,
                    stamp_base64=getattr(data, "stamp", None) or "",
                    signature_base64=getattr(data, "signature", None) or "",
                    barcode_base64=getattr(data, "barcode", None) or "",
                    school_logo_base64=getattr(data, "school_logo", None) or "",
                    color=getattr(data, "color", None) if data else None,
                    side_2_color=getattr(data, "side_2_color", "#00897B") if data else "#00897B",
                    background_color=getattr(data, "background_color", None) if data else None,
                    accent_color=getattr(data, "accent_color", None) if data else None,
                    color_overrides=getattr(data, "color_overrides", None) if data else None,
                    colors=getattr(data, "colors", None) if data else None,
                    student_code=getattr(data, "student_code", "") or "",
                    valid_thru=getattr(data, "valid_thru", "") or "09/30",
                    school_type=getattr(data, "school_type", "") or "HIGH SCHOOL",
                    school_slogan=getattr(data, "slogan", "") or "",
                    director_name=getattr(data, "director_name", "") or "",
                    director_contact=getattr(data, "director_contact", "") or "",
                )

            return None

        except KeyError as error:
            raise RuntimeError("byee")


if __name__ == "__main__":
    asyncio.run(fetch_schools())
