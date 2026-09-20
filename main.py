from card_sdk.card_agent import Card, Student
from card_sdk.template_manager import pull_template_files, pull_template_options
from card_sdk.card import image_file_to_base64
from card_sdk.base64qrcode import base64_barcode, base64_qrcode

import asyncio
import json

""" 

for uix template preview and template choice options
the implementing code here:

# to get templates available
 templates = pull_template_files()

# get template as ask option
template_options = pull_template_options()


"""

# --- Pick which installed SDK template to generate with ---------------------
# Prefer the BANK_INSPIRE template so the demo exercises the card-number
# variables; fall back to the first installed option otherwise.
templateName = pull_template_options()  # e.g. ['BLUE GREEN STUDENT CARD', ...]
template = None
if "BANK_INSPIRE" in templateName:
    template = "BANK_INSPIRE"
else:
    template = templateName[0] if templateName else None
if template is None:
    raise RuntimeError("No templates installed — run CardFly 'Save to SDK' first.")

# --- Configure the card data ------------------------------------------------
# Values set on the Student class (same pattern as before) are read when a
# real Student instance is built below.
Student.photo = "https://admin.kaascan.com/assets/2ebf1446-9312-4085-af4a-f53ac8aa75b6?download="
Student.name = "Tuyishimire Fraterine Ely"
Student.Class = "Software Devlopment"
Student.school_name = "KANYINYA TSS"
Student.student_id = "cvvvvvvv"
Student.student_code = "5298 7601 2345 6789"
Student.valid_thru = "09/30"
Student.school_type = "HIGH SCHOOL"
Student.template_name = template

# data_qrcode is rendered into a QR code on the card. It must be a *string*
# (the Student model validates it as str) — use the student id or any JSON.
Student.data_qrcode = json.dumps({"student_id": Student.student_id})

# --- Stamp, signature & barcode (rendered on the card) ----------------------
# image_file_to_base64() returns a bare base64 string; _img_href() now promotes
# it to a data-URI so the images are actually visible on the rendered card.
# base64_barcode() already returns a data-URI PNG, so it renders as-is.
Student.stamp = image_file_to_base64("draf/root/stamp.png")
Student.signature = image_file_to_base64("draf/root/signature.png")
# Director appears on the back of the card (name + contact).
Student.director_name = "GASHIRWANDEMELEHE JUVENAL"
Student.director_contact = "+250 78 000 0000"
Student.barcode = base64_barcode({"code": Student.student_code})

# --- Build a real Student instance from the values above (same field names) -
student = Student(
    photo=Student.photo,
    name=Student.name,
    Class=Student.Class,
    school_name=Student.school_name,
    data_qrcode=Student.data_qrcode,
    student_id=Student.student_id,
    student_code=Student.student_code,
    valid_thru=Student.valid_thru,
    school_type=Student.school_type,
    template_name=Student.template_name,
    stamp=Student.stamp,
    signature=Student.signature,
    barcode=Student.barcode,
    director_name=Student.director_name,
    director_contact=Student.director_contact,
)

asyncio.run(Card.agent(data=student))