import io
import re
import time
import zipfile

from color_cli import color_text
from jload import jload, jsave
import requests
import asyncio
import aiohttp
import pathlib
from cloud_unzip import RemoteZipExtractor
from prettytable import PrettyTable

tb = PrettyTable()
tb.header = False


base_dir = pathlib.Path(__file__).parent

# def check_file_corrupt(template_content: str) -> bool:

#     is_corrupt = False

#     check_contains = ["<svg>","</svg>","xml"]

#     for key_checks in check_contains:
#         # if patterns missing the template is corrupt
#         result = (re.search(key_checks,template_content) == None) != True
#         is_corrupt = (re.search(key_checks,template_content) == None)


#     return is_corrupt


# async def save_template(front_temple: str,back_template: str) -> bool:
#     with open(f"{base_dir}/templates/update/cache/front.card.kaascan.update","w+") as front_card_file:
#         front_card_file.write(front_temple)


#     with open(f"{base_dir}/templates/update/cache/back.card.kaascan.update","w+") as back_card_file:
#         back_card_file.write(back_template)

#     return True


# async def get_template(cloud_url: str) -> str:
#     async with aiohttp.ClientSession() as session:

#         svg_templates = []

#         result = await session.get(cloud_url)
#         svg_template = await result.content.read()

#         if check_file_corrupt(svg_template.decode("utf-8")):
#             print(color_text("[!] Template file corrupt | Please reach out to fix up.",color="red"))
#             exit()


#         return svg_template.decode("utf-8")


async def pull_card_patch() -> dict:
    try:
        url = "https://automation.kaascan.com/webhook/card/patch/updates"
        patchRes = requests.get(url, timeout=20)

        return patchRes.json()
    except Exception as error:

        print(f"pulling patch ::failed: {error}")
        return None


async def update_template() -> dict:
    try:
        # cloud_urls = {
        #     "card_front_template_url": "https://admin.kaascan.com/assets/3c4a1121-aa20-4e71-81e6-0d76293478c9?download=",
        #     "card_back_template_url": "https://admin.kaascan.com/assets/5e8f1f19-fc65-4ab4-8522-3b0a6fcfb849?download="
        # }

        # await save_template( front_temple = await get_template(cloud_urls["card_front_template_url"]),
        #                      back_template = await get_template(cloud_urls["card_back_template_url"]) )

        # print(color_text(f"[*] template up to date","green"))

        patch = await pull_card_patch()
        current_template_patch_version = jload(f"{base_dir}/templates/info.json")

        # Offline or unreachable patch server → keep the local templates and
        # carry on instead of aborting the whole job.
        if patch is None or "patch_file" not in patch:
            tb.add_row([color_text("patch-server", "white"), color_text("::unreachable — using local templates", "yellow")])
            print(tb)
            return {}

        patch_file_name = patch["patch_file"]
        print(current_template_patch_version[0]["template_patch_version"])

        if (
            patch_file_name
            != current_template_patch_version[0]["template_patch_version"]
        ):

            print(color_text(patch_file_name, "green"))

            url = f"https://automation.kaascan.com/webhook/download/card/patch/updates?patch={patch_file_name}"

            folder = f"{base_dir}/templates/templates_base"

            # Download and extract
            with_zip = requests.get(url, timeout=60)
            with_zip.raise_for_status()
            zipfile.ZipFile(io.BytesIO(with_zip.content)).extractall(folder)

            # keep template version info
            jsave(
                {"template_patch_version": patch_file_name},
                f"{base_dir}/templates/info.json",
                format="auto",
            )

        else:

            tb.add_row([color_text("check-pack-update", "white"),color_text("::template patch uptodate", "green")])
            print(tb)

    except Exception as update_error:

        tb.add_row([f"[-] updating failed (using local templates): {update_error}"])
        print(tb)
        return {}


if __name__ == "__main__":
    asyncio.run(update_template())
