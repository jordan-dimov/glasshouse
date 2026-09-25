"""The marking flows' payload discipline against a fake binary: the
version id is given back exactly when the claim provably did not land."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from glasshouse.commit import GlasshouseClient, MorphologError, MorphologOutcomeUnknown
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import register_curve_version
from glasshouse.compute.store import CurveStore
from tests.support import fake_binary

START = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
CURVE = HourlyCurve(tuple((START + dt.timedelta(hours=h), Decimal("70")) for h in range(24)))


@dataclass
class RecordingStore:
    saved: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def save(self, *, org: str, version: str, curve: HourlyCurve) -> None:
        self.saved.append(version)

    def discard(self, *, org: str, version: str) -> None:
        self.discarded.append(version)


def _register(binary: Path, store: RecordingStore) -> object:
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    return register_curve_version(
        client,
        cast("CurveStore", store),
        actor="alice",
        org="acme-energy",
        market="de-power",
        as_of=dt.date(2026, 7, 1),
        version="acme-energy/crv-1",
        curve=CURVE,
    )


def test_a_rejection_gives_the_version_back(tmp_path: Path) -> None:
    binary = fake_binary(
        tmp_path, json.dumps({"status": "rejected", "reason": "gate"}), exit_code=1
    )
    store = RecordingStore()
    _register(binary, store)
    assert store.discarded == ["acme-energy/crv-1"]


def test_a_known_non_commit_gives_the_version_back(tmp_path: Path) -> None:
    # The binary's own statement, by published code, that nothing was
    # recorded: since v0.0.12 that is the ONLY thing the client reads as
    # a known non-commit (an empty stdout on exit 1 no longer is).
    binary = fake_binary(
        tmp_path,
        json.dumps(
            {"status": "error", "code": "not_committed", "error": "the proposal was not committed"}
        ),
        stderr="Error: the proposal was not committed",
        exit_code=1,
    )
    store = RecordingStore()
    with pytest.raises(MorphologError) as raised:
        _register(binary, store)
    assert not isinstance(raised.value, MorphologOutcomeUnknown)
    assert store.discarded == ["acme-energy/crv-1"]


def test_silence_on_a_failing_exit_keeps_the_payload(tmp_path: Path) -> None:
    # Exit 1 with nothing on stdout used to be read as a known
    # non-commit. It is not one: the child may have died after COMMIT
    # was sent, so the payload stays.
    binary = fake_binary(tmp_path, "", stderr="Error: the proposal was not committed", exit_code=1)
    store = RecordingStore()
    with pytest.raises(MorphologOutcomeUnknown):
        _register(binary, store)
    assert (store.saved, store.discarded) == (["acme-energy/crv-1"], [])


def test_an_unknown_outcome_keeps_the_payload(tmp_path: Path) -> None:
    # Exit 3: COMMIT failed without a server verdict, so the claim may
    # have landed - discarding its payload could leave a claim anchoring
    # nothing.
    binary = fake_binary(tmp_path, "", stderr="Error: commit outcome unknown", exit_code=3)
    store = RecordingStore()
    with pytest.raises(MorphologOutcomeUnknown):
        _register(binary, store)
    assert (store.saved, store.discarded) == (["acme-energy/crv-1"], [])


def test_an_undecodable_reply_keeps_the_payload(tmp_path: Path) -> None:
    # Exit 0 with stdout that is not an outcome envelope: the proposal
    # may have committed. The v0.0.11 client raised the decoder's own
    # error here (recorded in contract section 24); since v0.0.12 the
    # generated one-shot client says `MorphologOutcomeUnknown`, and the
    # payload stays either way.
    binary = fake_binary(tmp_path, '{"status": "committed"', exit_code=0)
    store = RecordingStore()
    with pytest.raises(MorphologOutcomeUnknown):
        _register(binary, store)
    assert (store.saved, store.discarded) == (["acme-energy/crv-1"], [])
