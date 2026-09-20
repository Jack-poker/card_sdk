import random

from color_cli import color_text
from jload import jsave
import json
import os
import pathlib
import uuid

base_dir = pathlib.Path(__file__).parent




def pull_template_files() -> None:
    templates = []
    template_files = os.listdir(base_dir / "templates" / "templates_base")

    for index, file in enumerate(template_files, start=1):

        if os.path.isdir(f"{base_dir}/templates/templates_base/{file}"):
            template_file = pathlib.Path(
                base_dir / "templates" / "templates_base" / file
            )
            template_name = file
            templates.append(
                {
                    "filename": template_name,
                    "file_path": f"{base_dir}/templates/templates_base/{file}",
                    "front": f"{base_dir}/templates/templates_base/{file}/front.card.kaascan",
                    "back": f"{base_dir}/templates/templates_base/{file}/back.card.kaascan",
                }
            )

    return templates


def latest_patch_save(template_data: dict, template_name: str):

    with open(
        f"{base_dir}/templates/templates_base/templates.json"
    ) as templatebase_json_file:

        json_data = json.loads(templatebase_json_file.read())

        if json_data == []:
            jsave(
                template_data,
                f"{base_dir}/templates/templates_base/templates.json",
                append=True,
                format="auto",
            )

        for data in json_data:

            if data["template_file"][0]["filename"] == template_name:
                ...
            else:
                jsave(
                    template_data,
                    f"{base_dir}/templates/templates_base/templates.json",
                    append=True,
                    format="auto",
                )


def patch_templates() -> None:
    template_data = pull_template_files()
    template_name = template_data[0]["filename"]
    template_file_base = {"template_file": pull_template_files()}
    latest_patch_save(template_file_base, template_name)


def pull_template_options() -> list:
    template_data = pull_template_files()
    template_options = []

    text_colors = ["green", "blue", "yellow", "white", "red"]

    for data in template_data:
        template_options.append(data["filename"])

    return template_options


if __name__ == "__main__":
    print(pull_template_options())
