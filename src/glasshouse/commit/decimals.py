"""Numbers from text, as the ledger will hold them (law 8).

Every way in (a CSV row, an agent's proposal, a fill the reconciler
reports, a cap typed at the command line) reads its numbers here, so a
number the ledger cannot hold as written is refused for being that
number. Since Morpholog 0.0.19 the ledger refuses, rather than rounds,
a decimal past 28 places or 29 digits, and the generated codec refuses
it too; read later and under another field's name, that refusal named
the wrong thing.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from glasshouse.commit.morpholog_client import values

#: What `exact_decimal` refuses, in the words a refusal shows a desk.
EXACT_DECIMALS = "exact decimals (at most 28 decimal places and 29 digits)"


def exact_decimal(text: str) -> Decimal:
    """Text to the Decimal it spells, or `InvalidOperation`: only a
    finite number the ledger holds exactly as written passes."""
    number = Decimal(text)
    try:
        values.encode_decimal(number)
    except values.CodecError:
        raise InvalidOperation(text) from None
    return number


def exact_decimals(**texts: str) -> dict[str, Decimal]:
    """Several named fields at once, or one `ValueError` naming them all
    and what was written: `quantity/price must be exact decimals ...,
    got 'ten'/'86.25'`."""
    try:
        return {name: exact_decimal(text) for name, text in texts.items()}
    except InvalidOperation:
        names = "/".join(texts)
        written = "/".join(repr(text) for text in texts.values())
        raise ValueError(f"{names} must be {EXACT_DECIMALS}, got {written}") from None
