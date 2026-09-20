"""
Example usage of the card_sdk with CardConfig class.

This script demonstrates how to use the new CardConfig class for structured
configuration of card generation, including stamps, signatures, and other
branding elements.
"""

from card_sdk.card import (
    CardConfig,
    Student,
    generate_card,
    save_card,
    image_file_to_base64,
    image_url_to_base64,
    resize_image_base64,
)
from card_sdk.base64qrcode import base64_qrcode
import asyncio


def example_basic_usage():
    """Basic example using CardConfig."""
    print("=== Basic CardConfig Usage ===")
    
    # Create a CardConfig instance
    config = CardConfig(
        student_name="John Doe",
        student_class="Grade 10",
        school_name="Example High School",
        student_id="STU12345",
        photo="https://example.com/student_photo.jpg",  # Will be converted to base64
        data_qrcode=base64_qrcode({"student_id": "STU12345"}),
        template_name="BLUE GREEN STUDENT CARD",
    )
    
    print(f"Student Name: {config.student_name}")
    print(f"School: {config.school_name}")
    print(f"Template: {config.template_name}")
    
    return config


def example_with_branding():
    """Example with stamp, signature, and barcode."""
    print("\n=== CardConfig with Branding Elements ===")
    
    # Convert images to base64
    # stamp_base64 = image_file_to_base64("path/to/stamp.png")
    # signature_base64 = image_file_to_base64("path/to/signature.png")
    # barcode_base64 = image_file_to_base64("path/to/barcode.png")
    # logo_base64 = image_file_to_base64("path/to/school_logo.png")
    
    # For demo purposes, using placeholder base64 strings
    stamp_base64 = ""  # Replace with actual base64
    signature_base64 = ""  # Replace with actual base64
    barcode_base64 = ""  # Replace with actual base64
    logo_base64 = ""  # Replace with actual base64
    
    config = CardConfig(
        student_name="Jane Smith",
        student_class="Grade 12",
        school_name="Advanced Academy",
        student_id="STU67890",
        photo="https://example.com/student_photo2.jpg",
        data_qrcode=base64_qrcode({"student_id": "STU67890"}),
        template_name="BLUE GREEN STUDENT CARD",
        stamp=stamp_base64,
        signature=signature_base64,
        barcode=barcode_base64,
        school_logo=logo_base64,
        side_2_color="#0066CC",  # Custom back color
        snFontsize=3.0,  # Larger font size
        snx=-12.0,  # Custom X position
        sny=-1.0,  # Custom Y position
    )
    
    print(f"Student Name: {config.student_name}")
    print(f"Stamp provided: {bool(config.stamp)}")
    print(f"Signature provided: {bool(config.signature)}")
    print(f"Barcode provided: {bool(config.barcode)}")
    print(f"School Logo provided: {bool(config.school_logo)}")
    
    return config


def example_image_conversion():
    """Example of image conversion utilities."""
    print("\n=== Image Conversion Utilities ===")
    
    # Convert image file to base64
    # base64_string = image_file_to_base64("path/to/image.png")
    # print(f"Image converted to base64: {len(base64_string)} characters")
    
    # Convert image URL to base64
    # base64_string = image_url_to_base64("https://example.com/image.jpg")
    # print(f"URL image converted to base64: {len(base64_string)} characters")
    
    # Resize base64 image
    # resized_base64 = resize_image_base64(base64_string, max_width=100, max_height=100)
    # print(f"Resized image: {len(resized_base64)} characters")
    
    print("Image conversion utilities available:")
    print("  - image_file_to_base64(file_path)")
    print("  - image_url_to_base64(url)")
    print("  - resize_image_base64(image_base64, max_width, max_height)")


def example_backward_compatibility():
    """Example showing backward compatibility with Student model."""
    print("\n=== Backward Compatibility ===")
    
    # Create a Student instance (existing API)
    student = Student(
        photo="https://example.com/student_photo.jpg",
        name="Old Style Student",
        Class="Grade 9",
        school_name="Traditional School",
        data_qrcode="{}",
        student_id="OLD123",
        template_name="BLUE GREEN STUDENT CARD",
    )
    
    # Create CardConfig and convert to Student
    config = CardConfig(
        student_name="New Style Student",
        student_class="Grade 11",
        school_name="Modern School",
        student_id="NEW456",
        photo="https://example.com/student_photo2.jpg",
        data_qrcode=base64_qrcode({"student_id": "NEW456"}),
        template_name="BLUE GREEN STUDENT CARD",
    )
    
    # Convert CardConfig to Student for backward compatibility
    student_from_config = config.to_student()
    
    print(f"Original Student: {student.name}")
    print(f"CardConfig converted to Student: {student_from_config.name}")
    print(f"Both can be used with generate_card()")


if __name__ == "__main__":
    print("Card SDK Example Usage\n")
    
    # Run examples
    basic_config = example_basic_usage()
    branding_config = example_with_branding()
    example_image_conversion()
    example_backward_compatibility()
    
    print("\n=== Next Steps ===")
    print("1. Replace placeholder base64 strings with actual image data")
    print("2. Call generate_card() with the config parameters")
    print("3. Use save_card() to save the generated card")
    print("\nExample:")
    print("  card_content = basic_config.generate_card_content()")
    print("  save_card(card_content, 'card_id', 'class_folder', 'school_name')")