# CardFly Studio

Student card designer with 3D preview, variable binding, and a Python decorator bridge for automatic card interface generation.

## Quick start

### Run the editor (static server)

```bash
cd editor
python3 -m http.server 8131
# open http://localhost:8131
```

### Run the Python bridge (optional)

```bash
cd cardgen
python3 cardgen.py your_script.py
# bridge is live at http://127.0.0.1:8123
```

## Project structure

```
CARD_FLY/
  editor/           ← the card editor (static site)
    index.html      ← main editor page
    style.css       ← Bauhaus design system
    app.js          ← editor logic
    preview3d.js    ← 3D preview (Three.js)
    docs.html       ← documentation page (in-app)
    samples/        ← bundled sample card templates
    lib/            ← Three.js + addons
  cardgen/          ← Python decorator bridge
    cardgen.py      ← @card decorator + auto HTTP server
    example_card.py ← example generator script
  docs.html         ← (editor/docs.html)
```

## The `@card` decorator

Write a Python function that generates a card, decorate it with `@card`, and CardFly reads its variables automatically:

```python
from cardgen import card, Field, Image, Choice, serve

@card(name="Student Card", template="front.card.kaascan")
def generate_student_card(
    full_name: str,
    id_number: Field(str, label="Student ID", required=True, help="Unique ID."),
    year: int = 2025,
    photo: Image = Image,
    grade: Choice("A", "B", "C") = Choice("A", "B", "C"),
) -> str:
    return '<svg ...>...</svg>'

if __name__ == "__main__":
    serve()
```

### Variable detection

The decorator inspects the function signature and maps types to variables:

| Python | Becomes |
|---|---|
| `name: str` | Text variable (required) |
| `photo: Image` / `= Image` | Image variable |
| `grade: Choice("A","B")` | Fixed-choice variable |
| `x: int = 5` | Text variable, optional, default 5 |
| `id: Field(str, label="ID", required=True)` | Rich metadata: label, help, required |

### HTTP endpoints

When `serve()` runs, the bridge exposes:

| Route | Returns |
|---|---|
| `/` | Index JSON listing endpoints |
| `/__card__variable_contract__` | JSON contract of cards + variables |
| `/__card__docs__` | Documentation page (HTML) |

### CLI

```bash
python3 cardgen.py your_script.py              # serve contract over HTTP
python3 cardgen.py your_script.py --emit out.json  # emit contract to file
python3 cardgen.py --port 9000 your_script.py  # custom port
```

## Connecting the bridge to the editor

1. Run your generator: `python3 my_card.py`
2. Open CardFly Studio → Variables tab → Bridge → Listen
3. The editor fetches the contract and pre-fills the Variables tab

Or open `http://127.0.0.1:8123/__card__docs__` in a browser for full docs.

## 3D preview

Open the 3D preview to see the card as a physical object. The camera auto-fits the card to the viewport and keeps it centred on any screen size. Drag to orbit, scroll to zoom.

## Design

Bauhaus-inspired: primary geometry, hard shadows, stark black rules, no gradients. Yellow, red, blue on off-white canvas. Outfit font.
