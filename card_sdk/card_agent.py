import json
import os
import pathlib

from typing import Dict, Optional, Any

import ui
from card_sdk.terminal_kid import Tkd
from card_sdk.card import (
    clear_report_file,
    base_dir,
    single_image_url_to_base64,
    generate_card,
    Student,
    CardConfig,
    save_card,
    async_image_url_to_base64,
    image_file_to_base64,
    base64_qrcode,
    check_image_cache,
    _decode_urlencoded_placeholders,
    _img_href,
    _safe_format,
    _sanitize_font_families,
    report_missing,
)
import asyncio
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
from pathlib import Path
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
    try:
        # Get user data
        response = requests.get(
            data_url,
            headers={
                "accept": "application/json",
                "X-API-KEY": get_admin_api_key(),
            },
        )

        total_users = response.json()["count"]

        jsave({"total_users": total_users}, file_path=f"{base_dir}/report/users.json")

        return total_users

    except Exception as error:
        print(f"[-] something went wrong: {error}")


async def get_csrftoken() -> str:
    try:
        fetch_csrftoken = requests.get(
            "https://api.v2.kaascan.com/get-csrf-token"
        ).json()

        csrf_token = fetch_csrftoken["csrf_token"]

        return csrf_token

    except Exception as error:
        print(f"[-] fetching csrf token failed: {error}")


async def fetch_schools() -> list:
    try:

        fnd_schools = []
        fetch_schools = requests.get("https://automation.kaascan.com/webhook/schools")

        schools = fetch_schools.json()[0]

        for school_name in schools:
            fnd_schools.append(schools[school_name])

        return fnd_schools

    except Exception as error:
        print(f"[-] Fetching schools failed: {error}")


# students
async def fetch_userdata(data_url: str) -> dict:
    try:
        # Get user data
        response = requests.get(
            data_url,
            headers={
                "accept": "application/json",
                "X-API-KEY": get_admin_api_key(),
            },
        )
        user_data = response.json()["data"]
        total_users = response.json()["count"]

        # Process all students concurrently
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = []
            inc = 0
            for data in user_data:

                Tkd.checking_task_progress(
                    "Fetching users data", float(inc / (total_users / 100))
                )
                inc += 1

                # Get the total number of users to make the process for::

                # Create task for each student
                task = process_student(session, data, inc)
                tasks.append(task)

            print(color_text(f"[>] Fetching users data Completed.", "green"))
            # Execute all tasks at once
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

    except Exception as e:
        print(f"[*] FETCHING USER DATA FAILED: {e}")
        return []


def create_classFolder(school_folder_name: str, folder_name: str):
    if not os.path.exists(
        f"{pathlib.Path(__file__).parent.parent}/output/{school_folder_name}/{folder_name}"
    ):

        class_dir = (
            Path(__file__).parent.parent / "output" / school_folder_name / folder_name
        )
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
        "student_id": data["student_id"],
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


async def single_card(data: Student, stamp_base64: str = "", signature_base64: str = "", barcode_base64: str = "", school_logo_base64: str = "") -> str:

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
        director_name=getattr(data, "director_name", "") or "",
        director_contact=getattr(data, "director_contact", "") or "",
    )

    Tkd.checking_task_progress(result, 80)
    print(color_text("[-] Single card generated successful. \n \n", "light_green"))
    print(
        color_text(
            f"[-] Check output folder: {base_dir}/output/{data.school_name}/{data.Class}/ \n \n",
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
    import svg2pdf_py
    import pymupdf

    student_id = args.get("student_id", "card")
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
    except Exception:
        font_db = svg2pdf_py.FontDatabase()

    try:
        front_args = {
            "color": "#0d0000",
            "academic_year": "ACADEMIC YEAR",
            "school_subtitle": "subtitle here",
            "student_code": args.get("student_code", "") or "",
            "valid_thru": args.get("valid_thru", "") or "09/30",
            "school_type": args.get("school_type", "") or "HIGH SCHOOL",
            "snFontsize": args.get("snFontsize", 2.4932),
            "name": args["student_name"],
            "student_names": args["student_name"],
            "snx": args.get("snx", -10.523738),
            "sny": args.get("sny", -0.8769781),
            "Class": args["student_class"],
            "student_class": args["student_class"],
            "school_name": args["school_name"],
            "school_logo": _img_href(args.get("school_logo", "")),
            "image_base64": _img_href(args.get("image_base64", "")),
            "student_photo": _img_href(args.get("image_base64", "")),
            "side_2_color": args.get("side_2_color", "#00897B"),
            "data_qrcode": _img_href(args.get("data_qrcode", "")),
            "stamp_base64": _img_href(args.get("stamp_base64", "")),
            "student_barcode": _img_href(args.get("student_barcode", "")),
            "signature_base64": _img_href(args.get("signature_base64", "")),
            "director_name": args.get("director_name", "") or "",
            "director_contact": args.get("director_contact", "") or "",
        }
        front = _safe_format(_decode_urlencoded_placeholders(open(
            f"{base_dir}/templates/templates_base/{args['template_name']}/front.card.kaascan",
            encoding="utf-8",
        ).read()), front_args)
    except Exception as exc:
        return {"student_id": student_id, "ok": False, "error": f"front fill: {exc}"}
    try:
        back_args = {
            "data_qrcode": _img_href(args.get("data_qrcode", "")),
            "student_code": args.get("student_code", "") or "",
            "valid_thru": args.get("valid_thru", "") or "09/30",
            "school_type": args.get("school_type", "") or "HIGH SCHOOL",
            "student_barcode": _img_href(args.get("student_barcode", "")),
            "student_photo": _img_href(args.get("image_base64", "")),
            "snFontsize": args.get("snFontsize", 2.4932),
            "student_name": args["student_name"],
            "snx": args.get("snx", -10.523738),
            "sny": args.get("sny", -0.8769781),
            "student_class": args["student_class"],
            "school_name": args["school_name"],
            "school_logo": _img_href(args.get("school_logo", "")),
            "image_base64": _img_href(args.get("image_base64", "")),
            "name": args["student_name"],
            "Class": args["student_class"],
            "director_name": args.get("director_name", "") or "",
            "director_contact": args.get("director_contact", "") or "",
        }
        back = _safe_format(_decode_urlencoded_placeholders(open(
            f"{base_dir}/templates/templates_base/{args['template_name']}/back.card.kaascan",
            encoding="utf-8",
        ).read()), back_args)
    except Exception as exc:
        return {"student_id": student_id, "ok": False, "error": f"back fill: {exc}"}

    # Report any required element that wasn't supplied so it doesn't silently
    # vanish from the card (stamp / signature / barcode / logo / QR / photo).
    # ``data_qrcode`` is an alternative to ``student_barcode``: a card that
    # carries a barcode does not need QR data, so skip that check when a
    # barcode is present.
    try:
        _has_barcode = args.get("student_barcode") and str(args.get("student_barcode", "")).strip()
        _missing = [
            name
            for name, raw in (
                ("stamp_base64", args.get("stamp_base64")),
                ("signature_base64", args.get("signature_base64")),
                ("student_barcode", args.get("student_barcode")),
                ("school_logo", args.get("school_logo")),
                ("data_qrcode", args.get("data_qrcode")),
                ("image_base64", args.get("image_base64")),
            )
            if not (raw and str(raw).strip())
            and not (name == "data_qrcode" and _has_barcode)
        ]
        if _missing:
            report_missing(
                card_id=student_id,
                school_name=args.get("school_name", ""),
                student_class=args.get("student_class", ""),
                missing=_missing,
            )
    except Exception:
        pass

    out_dir = (
        pathlib.Path(__file__).parent.parent
        / "output"
        / args["school_name"]
        / args["student_class"]
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"final_student_card_{student_id}.pdf"

    try:
        front = _sanitize_font_families(front)
        back = _sanitize_font_families(back)
        pdf_pages = svg2pdf_py.svg_pages_to_pdfs([front, back], font_db)
        if not pdf_pages:
            print(f":: warning: svg-to-pdf produced no pages for {student_id}")
        doc = pymupdf.open()
        for pb in pdf_pages:
            part = pymupdf.open("pdf", pb)
            doc.insert_pdf(part)
            part.close()
        doc.save(str(out_pdf), garbage=0, deflate=False)
        doc.close()
    except Exception as exc:
        return {"student_id": student_id, "ok": False, "error": f"render: {exc}", "out": str(out_pdf)}
    return {"student_id": student_id, "ok": True, "out": str(out_pdf)}


async def multiple_cards(
    template_name: str, data_url="https://api.v2.kaascan.com/admin/students",
    stamp_base64: str = "", signature_base64: str = "", barcode_base64: str = "", school_logo_base64: str = ""
):
    # Fetch users data (photos downloaded concurrently via aiohttp)
    user_data = await fetch_userdata(data_url)
    if not user_data:
        return []

    tasks = []
    # A card that carries a barcode does not need a QR — drop any QR data that
    # was pre-generated so the QR placeholder stays empty on barcode cards.
    suppress_qr = bool(barcode_base64 and str(barcode_base64).strip())
    for data in user_data:
        if not data or "student_photo" not in data:
            continue
        student_id = data.get("student_id", "")
        tasks.append(
            {
                "template_name": template_name,
                "image_base64": data["student_photo"],
                "student_name": data.get("student_name", ""),
                "student_class": data.get("student_class", ""),
                "school_name": data.get("school_name", ""),
                "student_id": student_id,
                "data_qrcode": "" if suppress_qr else data.get("data_qrcode", ""),
                "stamp_base64": stamp_base64,
                "student_barcode": barcode_base64,
                "signature_base64": signature_base64,
                "school_logo": school_logo_base64,
            }
        )

    workers = max(1, min(os.cpu_count() or 1, 8))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_render_card_worker, tasks, chunksize=1))

    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    for r in ok:
        Tkd.inform_user(r["student_id"])
    for r in failed:
        Tkd.inform_user(f"failed {r['student_id']}: {r.get('error')}")
    return ok


def create_output_Folder():
    if not os.path.exists("output"):
        os.system("mkdir output")
    else:
        pass


class Card:

    async def authorize_api_user():
        """QR-code handshake: wait for the WS to POST a valid ``admin_api_key``.

        Starts the local auth endpoint, prints a scannable QR + the endpoint
        URL in the terminal, and blocks until the admin's web service posts
        ``{"admin_api_key": "..."}``. The received key authorizes the SDK and
        unlocks card generation (SINGLE / MULTIPLE).
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

    async def agent(data=None):
        # create output folder
        create_output_Folder()
        # make template update
        from card_sdk.update import update_template

        print(color_text("\n \n [>] υρᑯα𝗍𝗂𐓣𝗀‌ \n \n", "green"))
        await update_template()
        print("[*] updating Finished [1]")

        Tkd.cleaner()

        Tkd.hello()

        # Authenticate the API user via the terminal QR handshake before any
        # card generation — the admin API key is never embedded in code.
        await Card.authorize_api_user()

        # Optional: start the web UI so templates can be viewed over HTTP.
        if ui.ask_yes_no(color_text("Start the web server to view templates?", "blue")):
            Tkd.inform_user("Starting web server ... (press Ctrl+C to stop)")
            from card_sdk.web_server import serve_web_async

            await serve_web_async()
            return

        schools = await fetch_schools()

        school = ui.ask_choice("Choose school: ", schools)

        modes = ["SINGLE", "MULTIPLE"]

        mode: str = ui.ask_choice("Choose generating mode: ", modes)

        template: str = ui.ask_choice("Choose Template", pull_template_options())

        # Clear the report file
        clear_report_file()
        # get total users number
        await total_users()

        try:

            Tkd.hello()

            if mode.lower() == "single" and data != None:

                singleCard_result = await single_card(data)

            if mode.lower() == "multiple":
                multipleCards = await multiple_cards(template)

        except KeyError as error:
            raise RuntimeError("byee")


if __name__ == "__main__":
    asyncio.run(fetch_schools())
