"""The classroom load: twenty organisations trading at once on one ledger.

The workload morpholog#396 describes: the acceptance measurement for the
substrate's contention work. The same restored ledger, one binary against
the next, and the number upstream asked for is **retries per commit**.
`commits_per_second` includes the Glasshouse flows' own work on every
attempt (payload store, reads, MTM), so it is a workload figure, not the
substrate's throughput.

Two commands:

    classroom_load.py build --database-url URL --filler 500 --procs 8
        Provision (this checkout's `glasshouse provision`, so the binary the
        checkout is pinned to lays down the schema and its indexes), then
        populate `--filler` organisations with one capture grant and ten
        captures each - 500 of them is the "10k" ledger (10,500 claims) -
        and ANALYZE. Dump it afterwards with pg_dump -Fc; both sides of a
        comparison restore the same dump.

    classroom_load.py burst --database-url URL --orgs 20 --trades 10 --out results.json
        Re-provision (idempotent; on a restored dump from an older binary
        this is the upgrade: migrate, then provision indexes), ANALYZE (so
        both sides start from fresh statistics whatever their provisioning
        did), then
        run `--orgs` processes at once, each an organisation's Monday and
        Tuesday: three capability grants, one official curve, ten captures,
        ten marks, then the correction and re-marking of every trade as one
        `transact`. Every decision goes through the Glasshouse flows
        (payload store, then claim), one-shot `propose` each, retried with
        jittered backoff on a serialization failure up to `--max-attempts`
        times. A sampler thread reads pg_locks at 4 Hz and reports how often
        a relation-level SIRead lock on `morpholog.claims` was present.

Run it from the checkout whose client and pinned binary you are
measuring (`GLASSHOUSE_MORPHOLOG_BIN` set), against a disposable
database: `build` and `burst` both write governed traffic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import multiprocessing as mp
import random
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from pathlib import Path

import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, MorphologError, models
from glasshouse.commit.morpholog_client import MORPHOLOG_VERSION
from glasshouse.commit.morpholog_client.envelopes import AtomicCommitted
from glasshouse.compute.marking import correct_and_remark, register_curve_version, value_trade
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.compute.terms import terms_version_id
from glasshouse.provision import run_provision
from glasshouse.seed import AS_OF, CORRECTED_CURVE, CURVE, DAY, MARKET, TRADE_DATE

# The demo's own market, day, curve and Tuesday correction (glasshouse.seed),
# so each organisation's story is the demo's story, twenty times at once.
BOOK = "spec"


@dataclass
class Tally:
    """Attempts per committed decision, by kind, from one process; the
    decisions that never committed, by kind; the substrate's refusals."""

    attempts: dict[str, list[int]] = field(default_factory=dict)
    gave_up: Counter[str] = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)

    def record(self, kind: str, attempts: int, *, committed: bool) -> None:
        if committed:
            self.attempts.setdefault(kind, []).append(attempts)
        else:
            self.gave_up[kind] += 1


class GaveUpError(Exception):
    """One organisation's story stopped: a decision that could not commit."""


def _client(database_url: str) -> GlasshouseClient:
    return GlasshouseClient(str(MODEL_FILE), database_url)


def _retrying[T](tally: Tally, kind: str, attempt: Callable[[], T], *, max_attempts: int) -> T:
    """Run one decision until it commits or the substrate refuses it for a
    reason a retry cannot fix. `MorphologError.retriable` is true for the
    serialization-failure receipt and nothing else; the backoff is jittered
    so twenty processes do not collide again in lockstep."""
    for n in range(1, max_attempts + 1):
        try:
            outcome = attempt()
        except MorphologError as failure:
            if not failure.retriable:
                tally.failures.append(f"{kind}: {failure}")
                tally.record(kind, n, committed=False)
                raise GaveUpError(kind) from failure
            time.sleep(random.uniform(0.01, 0.05) * min(n, 8))
            continue
        if not isinstance(outcome, Committed | AtomicCommitted):
            tally.failures.append(f"{kind}: rejected {outcome!r}")
            tally.record(kind, n, committed=False)
            raise GaveUpError(kind)
        tally.record(kind, n, committed=True)
        return outcome
    tally.record(kind, max_attempts, committed=False)
    raise GaveUpError(kind)


def _capture(client: GlasshouseClient, org: str, actor: str, trade: str, i: int) -> object:
    return client.submit(
        models.CaptureTradeRequest(
            org=org,
            book=BOOK,
            trade=trade,
            counterparty="cp-" + str(i % 3),
            market=MARKET,
            direction="buy" if i % 2 else "sell",
            version=terms_version_id(trade, 1),
            quantity=Decimal(str(5 + i)),
            price=Decimal(str(80 + i)),
            delivery_start=DAY + dt.timedelta(hours=i),
            delivery_end=DAY + dt.timedelta(hours=i + 4),
            trade_date=TRADE_DATE,
        ),
        actor=actor,
    )


def _filler(database_url: str, orgs: list[str], max_attempts: int) -> Tally:
    tally = Tally()
    client = _client(database_url)
    for org in orgs:
        actor = f"trader-{org}"
        try:
            grant = models.GrantCaptureAuthorityRequest(principal=actor, org=org, book=BOOK)
            _retrying(
                tally,
                "grant",
                partial(client.submit, grant, actor="bootstrap"),
                max_attempts=max_attempts,
            )
            for i in range(10):
                _retrying(
                    tally,
                    "capture",
                    partial(_capture, client, org, actor, f"{org}-T{i:03d}", i),
                    max_attempts=max_attempts,
                )
        except GaveUpError:
            continue
    return tally


def _story(database_url: str, org: str, trades: int, max_attempts: int) -> Tally:
    """One organisation's Monday and Tuesday, every decision retried."""
    tally = Tally()
    client = _client(database_url)
    engine = sa.create_engine(engine_url(database_url))
    store = CurveStore(engine)
    trader, curator, risk = f"trader-{org}", f"curator-{org}", f"risk-{org}"
    try:
        for request in (
            models.GrantCaptureAuthorityRequest(principal=trader, org=org, book=BOOK),
            models.GrantCurveAuthorityRequest(principal=curator, org=org, market=MARKET),
            models.GrantValuationAuthorityRequest(principal=risk, org=org, book=BOOK),
        ):
            _retrying(
                tally,
                "grant",
                partial(client.submit, request, actor="bootstrap"),
                max_attempts=max_attempts,
            )
        _retrying(
            tally,
            "register",
            lambda: register_curve_version(
                client,
                store,
                actor=curator,
                org=org,
                market=MARKET,
                as_of=AS_OF,
                version=f"{org}-v1",
                curve=CURVE,
            ),
            max_attempts=max_attempts,
        )
        ids = [f"{org}-T{i:03d}" for i in range(trades)]
        for i, trade in enumerate(ids):
            _retrying(
                tally,
                "capture",
                partial(_capture, client, org, trader, trade, i),
                max_attempts=max_attempts,
            )
        for trade in ids:
            _retrying(
                tally,
                "value",
                partial(value_trade, client, store, actor=risk, org=org, book=BOOK, trade=trade),
                max_attempts=max_attempts,
            )
        _retrying(
            tally,
            "transact",
            lambda: correct_and_remark(
                client,
                store,
                curve_actor=curator,
                valuation_actor=risk,
                org=org,
                market=MARKET,
                as_of=AS_OF,
                prior_version=f"{org}-v1",
                new_version=f"{org}-v2",
                curve=CORRECTED_CURVE,
            ),
            max_attempts=max_attempts,
        )
    except GaveUpError:
        pass
    finally:
        engine.dispose()
    return tally


def _sample_locks(engine: sa.Engine, stop: threading.Event, samples: list[bool]) -> None:
    """Whether a relation-level SIRead lock on the claims table is held,
    sampled at 4 Hz. The engine is AUTOCOMMIT: a sampler sitting in an
    open transaction between samples would pin xmin for the whole burst
    and skew the very contention it is measuring."""
    query = sa.text(
        "select exists(select 1 from pg_locks where locktype = 'relation' "
        "and mode = 'SIReadLock' and relation = 'morpholog.claims'::regclass)"
    )
    with engine.connect() as connection:
        while not stop.is_set():
            samples.append(bool(connection.execute(query).scalar()))
            time.sleep(0.25)


@dataclass(frozen=True)
class Merged:
    commits: int
    retries: int
    by_kind: dict[str, dict[str, float | int]]
    gave_up: dict[str, int]
    failures: list[str]

    def as_json(self) -> dict[str, object]:
        return {
            "commits": self.commits,
            "retries": self.retries,
            "retries_per_commit": round(self.retries / self.commits, 3) if self.commits else None,
            "by_kind": self.by_kind,
            "gave_up": self.gave_up,
            "gave_up_total": sum(self.gave_up.values()),
            "failures": self.failures[:20],
        }


def _merge(tallies: list[Tally]) -> Merged:
    attempts: dict[str, list[int]] = {}
    for tally in tallies:
        for kind, values in tally.attempts.items():
            attempts.setdefault(kind, []).extend(values)
    gave_up = sum((tally.gave_up for tally in tallies), Counter[str]())
    by_kind: dict[str, dict[str, float | int]] = {
        kind: {
            "commits": len(values),
            "p50_attempts": statistics.median(values),
            "p90_attempts": statistics.quantiles(values, n=10)[-1]
            if len(values) > 1
            else values[0],
            "mean_attempts": round(statistics.fmean(values), 2),
            "gave_up": gave_up[kind],
        }
        for kind, values in sorted(attempts.items())
    }
    return Merged(
        commits=sum(len(v) for v in attempts.values()),
        retries=sum(sum(v) - len(v) for v in attempts.values()),
        by_kind=by_kind,
        gave_up=dict(gave_up),
        failures=[f for tally in tallies for f in tally.failures],
    )


def _analyze(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        connection.execute(sa.text("analyze morpholog.claims"))


def _claims(engine: sa.Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sa.text("select count(*) from morpholog.claims")).scalar_one()
        )


def _binary_version(client: GlasshouseClient) -> str:
    """`--version` of the binary the client resolved (one discovery rule,
    the client's); on a client whose first-use check ran it equals the
    package's stamp by construction."""
    return subprocess.run(
        [client.binary, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()


def build(args: argparse.Namespace) -> None:
    report = run_provision(args.database_url)
    print(report.render())
    engine = sa.create_engine(engine_url(args.database_url), isolation_level="AUTOCOMMIT")
    orgs = [f"filler-{i:04d}" for i in range(args.filler)]
    chunks = [orgs[i :: args.procs] for i in range(args.procs)]
    started = time.monotonic()
    try:
        with mp.Pool(args.procs) as pool:
            tallies = pool.starmap(
                _filler, [(args.database_url, chunk, args.max_attempts) for chunk in chunks]
            )
        _analyze(engine)
        print(
            json.dumps(
                {
                    "built": args.filler,
                    "claims": _claims(engine),
                    "seconds": round(time.monotonic() - started, 1),
                    **_merge(tallies).as_json(),
                },
                indent=2,
            )
        )
    finally:
        engine.dispose()


def burst(args: argparse.Namespace) -> None:
    report = run_provision(args.database_url)
    print(report.render())
    engine = sa.create_engine(engine_url(args.database_url), isolation_level="AUTOCOMMIT")
    try:
        _analyze(engine)
        claims_before = _claims(engine)
        orgs = [f"class-{i:02d}" for i in range(args.orgs)]
        samples: list[bool] = []
        stop = threading.Event()
        sampler = threading.Thread(target=_sample_locks, args=(engine, stop, samples), daemon=True)
        sampler.start()
        started = time.monotonic()
        with mp.Pool(args.orgs) as pool:
            tallies = pool.starmap(
                _story, [(args.database_url, org, args.trades, args.max_attempts) for org in orgs]
            )
        elapsed = time.monotonic() - started
        stop.set()
        sampler.join(timeout=2)
        merged = _merge(tallies)
        result = {
            "label": args.label,
            "binary": _binary_version(_client(args.database_url)),
            "client": MORPHOLOG_VERSION,
            "orgs": args.orgs,
            "trades_per_org": args.trades,
            "claims_before": claims_before,
            "claims_after": _claims(engine),
            "seconds": round(elapsed, 1),
            "commits_per_second": round(merged.commits / elapsed, 2) if elapsed else None,
            "relation_siread_on_claims_present": round(sum(samples) / len(samples), 3)
            if samples
            else None,
            "lock_samples": len(samples),
            **merged.as_json(),
        }
    finally:
        engine.dispose()
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--max-attempts", type=int, default=30)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--filler", type=int, default=500)
    b.add_argument("--procs", type=int, default=8)
    b.set_defaults(run=build)
    r = sub.add_parser("burst")
    r.add_argument("--orgs", type=int, default=20)
    r.add_argument("--trades", type=int, default=10)
    r.add_argument("--label", default="")
    r.add_argument("--out", default="")
    r.set_defaults(run=burst)
    args = parser.parse_args(argv)
    args.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
