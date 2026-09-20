import qrcode
from qrcode.util import QRData, MODE_8BIT_BYTE
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import (
    RoundedModuleDrawer, 
    CircleModuleDrawer,
    GappedSquareModuleDrawer,
    VerticalBarsDrawer,
    HorizontalBarsDrawer,
    SquareModuleDrawer
)
from qrcode.image.styles.colormasks import (
    SolidFillColorMask,
    RadialGradiantColorMask,
    SquareGradiantColorMask,
    HorizontalGradiantColorMask,
    VerticalGradiantColorMask
)

import base64
import json
import re
from io import BytesIO
import pathlib

from cool_qrcode import make_cool_qrcode
from PIL import Image
from barcode import EAN13,Code128
from barcode.writer import ImageWriter

base_dir = pathlib.Path(__file__).parent







def base64_qrcode(qrcode_content: dict,selected_style = "4",front_color = "aad400ff") -> str:

  # An empty payload encodes to "{}" or "" which is not a real QR — a card
  # that uses a barcode leaves the QR data empty, so return nothing.
  if not qrcode_content:
    return ""
  if isinstance(qrcode_content, dict) and not qrcode_content:
    return ""
  if isinstance(qrcode_content, str) and not qrcode_content.strip():
    return ""

  qr_styles = {
    "1": {
        "name": "Rounded Navy",
        "drawer": RoundedModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(255, 255, 255)
        ),
        "description": "Rounded squares, dark navy. Clean."
    },
    "2": {
        "name": "Ocean Circles",
        "drawer": CircleModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(255, 255, 255)
        ),
        "description": "Circular dots, ocean blue. Modern."
    },
    "3": {
        "name": "Forest Green",
        "drawer": GappedSquareModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(240, 255, 240)
        ),
        "description": "Gapped squares, green. Organic."
    },
    "4": {
        "name": "Sunset Warm",
        "drawer": RoundedModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(255, 245, 240)
        ),
        "description": "Rounded, warm orange. Energetic."
    },
    "5": {
        "name": "Minimal Bars",
        "drawer": VerticalBarsDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(255, 255, 255)
        ),
        "description": "Vertical bars, pure B&W. Minimalist."
    },
    "6": {
        "name": "Luxury Gold",
        "drawer": RoundedModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=(180, 140, 50),
            back_color=(250, 245, 235)
        ),
        "description": "Rounded gold on cream. Premium."
    },
    "7": {
        "name": "Qaad Purple",
        "drawer": CircleModuleDrawer(),
        "color": SolidFillColorMask(
            front_color=tuple(int(f"{front_color}"[i:i+2], 16) for i in (0, 2, 4, 6)),
            back_color=(255, 255, 255)
        ),
        "description": "Circular dots, Qaad purple. Brand."
    },
}

  qr =  qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_L,
    box_size=10,
    border=4,
    )

  if isinstance(qrcode_content, dict):
      qrcode_content = json.dumps(qrcode_content, ensure_ascii=True, sort_keys=True)

  qr.add_data(qrcode_content)
  qr.make(fit=True)


  for key, style in qr_styles.items():
    # print(f"  [{key}] {style['name']} - {style['description']}")
    pass

  SELECTED = selected_style # <--- CHANGE THIS to try different styles (1-7)
  style = qr_styles[SELECTED]
  
  img = qr.make_image(
    image_factory=StyledPilImage,
    module_drawer=style["drawer"],
    color_mask=style["color"],
    embeded_image=None,  # Add logo later if needed
)
  buffer = BytesIO()
  img.save(buffer, format="PNG")  # Save as PNG to buffer
  img_bytes = buffer.getvalue()   # Get the PNG-encoded bytes

  base64Qrcode = f"data:image/png;base64,{base64.b64encode(img_bytes).decode('utf-8')}"
  return base64Qrcode




from barcode import EAN13
from barcode.writer import ImageWriter
from io import BytesIO
import base64
from PIL import Image
import numpy as np

def base64_barcode(barcode_content: dict, selected_style="4", front_color="aad400ff") -> str:
    """
    Generate a base64 encoded barcode image from content.

    The barcode always encodes the *digits* of the student code: anything that
    is not a digit (spaces, dashes, hex letters from a UUID, …) is stripped, so
    a code like "5298 7601 2345 6789" becomes "5298760123456789" (Code 128).
    """
    if 'code' not in barcode_content:
        raise ValueError("barcode_content must contain 'code' key")

    barcode_number = re.sub(r"[^0-9]", "", str(barcode_content['code']))
    if not barcode_number:
        raise ValueError(
            "barcode_content 'code' must contain digits (the student code)."
        )
    
    # Create a temporary file-like object
    buffer = BytesIO()
    
    # Use a custom writer that extends ImageWriter and overrides text painting
    class NoTextImageWriter(ImageWriter):
        def _paint_text(self, code, *args, **kwargs):
            # Intentionally do nothing - removes text
            pass
    
    # Generate barcode
    writer = NoTextImageWriter()
    writer.set_options({
        'quiet_zone': 0,
        'background': 'rgba(0,0,0,0)',  # Transparent background
        'foreground': 'rgb(0,0,0)',     # Black barcode
        'module_width': 0.2,
        'module_height': 15.0,
    })
    
# Write to buffer
    Code128(barcode_number, writer=writer).write(buffer)

    # Process the image to remove background and text
    buffer.seek(0)
    img = Image.open(buffer).convert('RGBA')

    # Convert to numpy for processing
    img_array = np.array(img)

    # Find the barcode bars (non-white/transparent pixels)
    # Create a mask for dark pixels (the barcode)
    # Dark pixels have low RGB values
    rgb = img_array[:, :, :3]

    # Find dark pixels (threshold can be adjusted)
    dark_mask = np.sum(rgb, axis=2) < 300  # Dark pixels have low sum

    # Create a new image with only the dark pixels, everything else transparent
    new_img = Image.new('RGBA', img.size, (0, 0, 0, 0))
    new_img_array = np.array(new_img)

    # Set dark pixels to black with full opacity
    new_img_array[dark_mask] = [0, 0, 0, 255]

    # Convert back to PIL Image
    result_img = Image.fromarray(new_img_array, 'RGBA')

    # Crop to the barcode content (remove empty space)
    bbox = result_img.getbbox()
    if bbox:
        result_img = result_img.crop(bbox)

    # Save the result straight to a buffer
    output_buffer = BytesIO()
    result_img.save(output_buffer, format='PNG', optimize=True)

    img_bytes = output_buffer.getvalue()
    return f"data:image/png;base64,{base64.b64encode(img_bytes).decode('utf-8')}"



# base64_barcode(barcode_content={
#    "code":123
# })