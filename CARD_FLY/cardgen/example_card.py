"""Example CardFly card generator using the @card decorator.

Run:

    python3 my_card.py

cardgen will detect the variables below and serve them over HTTP for
CardFly Studio to import into the editor's Variables tab.

    1. open CardFly Studio (editor/index.html)
    2. go to the Variables tab -> "Bridge" -> Listen on http://127.0.0.1:8123
    3. the variables declared here appear automatically
"""

from cardgen import card, Field, Image, Choice, serve


@card(
    name="Student Card",
    description="A student ID card from a CardFly generator.",
    template="front.card.kaascan",
)
def generate_student_card(
    full_name: str,
    id_number: Field(str, label="Student ID", required=True, help="Unique student/registration number."),
    year: int = 2025,
    photo: Image = Image,
    grade: Choice("A", "B", "C", "D") = Choice("A", "B", "C", "D"),
    active: bool = True,
) -> str:
    """Render a student card SVG for the given variables."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="85.6mm" height="54mm" '
        'viewBox="0 0 85.6 54">'
        f'<text x="5" y="12">{full_name}</text>'
        f'<text x="5" y="20">ID {id_number}</text>'
        f'<text x="5" y="28">{year}</text>'
        f'<text x="5" y="36">{grade}</text>'
        "</svg>"
    )


if __name__ == "__main__":
    serve()
