"""Three files in this repository are produced by the morpholog binary
and committed byte-exact: the pin that names which binary, the generated
Python client, and the generated view surface. Regenerating is one act
but three edits, and doing two of them is the mistake worth catching.

The live legs already close this by regenerating against a real binary
and diffing. These tests close the same gap where no binary is reachable
- CI's pure leg, and a laptop - so a half-done regeneration is named at
the point someone makes it rather than at the next integration run. The
same discipline as the pinned checkpoint argv (contract doc section 22):
a fact worth relying on is worth a test that does not need a database.
"""

from pathlib import Path

from glasshouse.commit import MODEL_HASH, VIEWS_FILE
from glasshouse.commit.morpholog_client import MORPHOLOG_VERSION

INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install-morpholog.sh"


def test_the_pin_and_the_generated_client_name_the_same_release() -> None:
    # A re-pin that bumps the installer without regenerating leaves a
    # client built by one binary and installed by another.
    pinned = next(
        line.removeprefix("VERSION=")
        for line in INSTALLER.read_text().splitlines()
        if line.startswith("VERSION=")
    )
    assert pinned == f"v{MORPHOLOG_VERSION}"


def test_the_view_surface_and_the_client_name_the_same_programme() -> None:
    # Law 4's inspection model and the commit layer over it are one
    # programme. The script stamps the hash into its catalogue rows, so
    # regenerating only one of the pair would put a view surface in front
    # of rules it was not generated from - which the live leg catches
    # only once a database has had the script applied to it.
    assert MODEL_HASH in VIEWS_FILE.read_text()
