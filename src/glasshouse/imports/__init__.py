"""The import path: CSV files in, governed proposals out, a report back.

An import's normal outcome is partial admission. Rows that cannot
honestly become proposals are quarantined locally with their reasons
(never silently coerced); rows the ledger refuses are lawful rejections;
both sit beside the committed rows in one report. Trades travel as one
`run --batch` invocation (each row its own transition); curves go
through the payload store first, so a committed claim never anchors
missing content.
"""

from glasshouse.imports.curves import import_curves, parse_curves
from glasshouse.imports.report import ImportReport, RowOutcome
from glasshouse.imports.trades import ImportFormatError, import_trades, parse_trades

__all__ = [
    "ImportFormatError",
    "ImportReport",
    "RowOutcome",
    "import_curves",
    "import_trades",
    "parse_curves",
    "parse_trades",
]
