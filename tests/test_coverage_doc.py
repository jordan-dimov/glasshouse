"""The coverage table's shape. The page is a public claim about what
Glasshouse does, so its rows must each carry one of the four states it
defines, and a row that says work is planned or under way must point at
the issue that says so.
"""

import re

from tests.support import ROOT

PAGE = (ROOT / "docs" / "coverage.md").read_text()
STATES = ("Has", "Building", "Planned", "Will not do", "Will not do, for now")
ISSUE = re.compile(r"\[#(\d+)\]\(https://github\.com/jordan-dimov/glasshouse/issues/(\d+)\)")


def rows() -> list[tuple[str, str, str]]:
    found = []
    for line in PAGE.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 3 and cells[0] not in ("Function", "---"):
            found.append((cells[0], cells[1], cells[2]))
    return found


def test_every_row_has_one_of_the_four_states() -> None:
    table = rows()
    assert len(table) > 40
    assert {state for _, state, _ in table} <= set(STATES)
    # All four are in use: a page with nothing left out, or nothing
    # planned, would not be the honest page.
    assert {"Has", "Building", "Planned", "Will not do"} <= {state for _, state, _ in table}


def test_planned_and_building_rows_name_their_issue() -> None:
    for function, state, notes in rows():
        if state in ("Planned", "Building"):
            assert ISSUE.search(notes), function


def test_issue_links_say_the_number_they_point_at() -> None:
    links = ISSUE.findall(PAGE)
    assert links
    assert all(shown == target for shown, target in links)


def test_no_row_promises_a_date() -> None:
    assert not re.search(
        r"\b(20\d\d|Q[1-4]|next (month|quarter|year)|by (January|February|March))\b", PAGE
    )
