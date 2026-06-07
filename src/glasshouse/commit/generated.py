"""Typed commit models for morpholog programme `glasshouse`. GENERATED - DO NOT EDIT.

Regenerate with scripts/generate_commit_models.py; the manifest
is the single input and the model hash pins the rules in force.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import ClassVar

from pydantic import AwareDatetime

from glasshouse.commit.bases import ClaimRow, CommitRequest


class AdmitValuation(CommitRequest):
    """Request for transformation `admit_valuation`."""

    TRANSFORMATION: ClassVar[str] = "admit_valuation"

    org: str
    book: str
    trade: str
    curve_version: str
    mtm_value: Decimal


class CaptureTrade(CommitRequest):
    """Request for transformation `capture_trade`."""

    TRANSFORMATION: ClassVar[str] = "capture_trade"

    org: str
    book: str
    trade: str
    counterparty: str
    market: str
    direction: str
    quantity_mw: Decimal
    price: Decimal
    delivery_start: AwareDatetime
    delivery_end: AwareDatetime


class CorrectCurve(CommitRequest):
    """Request for transformation `correct_curve`."""

    TRANSFORMATION: ClassVar[str] = "correct_curve"

    org: str
    market: str
    as_of: dt.date
    prior_version: str
    new_version: str
    payload_hash: str


class GrantCaptureAuthority(CommitRequest):
    """Request for transformation `grant_capture_authority`."""

    TRANSFORMATION: ClassVar[str] = "grant_capture_authority"

    principal: str
    org: str
    book: str


class GrantCurveAuthority(CommitRequest):
    """Request for transformation `grant_curve_authority`."""

    TRANSFORMATION: ClassVar[str] = "grant_curve_authority"

    principal: str
    org: str
    market: str


class GrantValuationAuthority(CommitRequest):
    """Request for transformation `grant_valuation_authority`."""

    TRANSFORMATION: ClassVar[str] = "grant_valuation_authority"

    principal: str
    org: str
    book: str


class RegisterCurve(CommitRequest):
    """Request for transformation `register_curve`."""

    TRANSFORMATION: ClassVar[str] = "register_curve"

    org: str
    market: str
    as_of: dt.date
    version: str
    payload_hash: str


class MayCaptureTradeClaim(ClaimRow):
    """Read model for predicate `MayCaptureTrade`."""

    PREDICATE: ClassVar[str] = "MayCaptureTrade"

    actor: str
    org: str
    book: str


class MayRegisterCurveClaim(ClaimRow):
    """Read model for predicate `MayRegisterCurve`."""

    PREDICATE: ClassVar[str] = "MayRegisterCurve"

    actor: str
    org: str
    market: str


class MayValueTradeClaim(ClaimRow):
    """Read model for predicate `MayValueTrade`."""

    PREDICATE: ClassVar[str] = "MayValueTrade"

    actor: str
    org: str
    book: str


class TradeCapturedClaim(ClaimRow):
    """Read model for predicate `TradeCaptured`."""

    PREDICATE: ClassVar[str] = "TradeCaptured"

    org: str
    book: str
    trade: str
    counterparty: str
    market: str
    direction: str


class TradeTermsClaim(ClaimRow):
    """Read model for predicate `TradeTerms`."""

    PREDICATE: ClassVar[str] = "TradeTerms"

    org: str
    trade: str
    quantity_mw: Decimal
    price: Decimal
    delivery_start: AwareDatetime
    delivery_end: AwareDatetime


class CurveRegisteredClaim(ClaimRow):
    """Read model for predicate `CurveRegistered`."""

    PREDICATE: ClassVar[str] = "CurveRegistered"

    org: str
    market: str
    as_of: dt.date
    version: str
    payload_hash: str


class OfficialCurveClaim(ClaimRow):
    """Read model for predicate `OfficialCurve`."""

    PREDICATE: ClassVar[str] = "OfficialCurve"

    org: str
    market: str
    as_of: dt.date
    version: str


class CurveSupersedesClaim(ClaimRow):
    """Read model for predicate `CurveSupersedes`."""

    PREDICATE: ClassVar[str] = "CurveSupersedes"

    new_version: str
    prior_version: str


class TradeValuedClaim(ClaimRow):
    """Read model for predicate `TradeValued`."""

    PREDICATE: ClassVar[str] = "TradeValued"

    org: str
    book: str
    trade: str
    curve_version: str
    mtm_value: Decimal


MODEL_HASH = "sha256:255fb8cd928645bfae2abf111840fc3cce0ad8258e134263a752428af9cb902a"
PROGRAM = "glasshouse"
