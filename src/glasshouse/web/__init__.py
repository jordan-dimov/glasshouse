"""The server-rendered operator UI: the Glasshouse Control Room.

Jinja2 + HTMX over the same query layer the JSON API renders (UI law 4:
every screen is a rendering of a public API query, no private UI
queries). Light, dense, calm; tabular numerals wherever money or
quantity appears; every identifier a piece of visible evidence. The
templates and static assets live inside this package so they ship in
the wheel and the Docker image alike.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
