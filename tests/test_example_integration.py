"""The example in `examples/limit_refusal.py`, run as a reader would run
it, against a freshly seeded book: an example nothing runs is an example
that stops working. Its own module, because it fills the hour it trades
in up to the cap.
"""

import os
import subprocess
import sys

from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.seed import seed_demo
from tests.support import BINARY, DB, ROOT, needs_live_stack, provision

pytestmark = needs_live_stack


def test_the_example_buys_until_the_limit_refuses_and_prints_the_refusal() -> None:
    engine = provision()
    seed_demo(GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY)), CurveStore(engine), engine)
    engine.dispose()
    ran = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "limit_refusal.py")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
        env=os.environ | {"GLASSHOUSE_DATABASE_URL": DB, "GLASSHOUSE_MORPHOLOG_BIN": str(BINARY)},
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    out = ran.stdout
    # What docs/mcp.md shows, line for line.
    assert "spec-de, hour 2026-07-01 12:00 UTC: holding 20 MW, cap 25 MW" in out
    assert out.count("buy 2 MW: accepted as agent") == 2
    assert "buy 2 MW: REFUSED" in out
    assert "rule:     hour_within_limit" in out
    assert "figures:  26 MW <= 25 MW does not hold" in out
    assert "hour:     2026-07-01T12:00:00+00:00" in out
