# Scope: what Glasshouse is, and is not

Glasshouse is the **book of record** for European power trading operations. So that nobody mistakes ambition for certification, this file states the boundaries plainly.

## Glasshouse is not

- **An optimiser.** It does not decide when to charge a battery or which market to bid into. Optimisers stay external; the lifecycle events that make their numbers legally meaningful land here.
- **A forecasting system or a price source.** Curves are registered, versioned and given official standing here; they are produced elsewhere.
- **A tick store or data platform.** High-volume market data, telemetry and EMS streams live in purpose-built stores downstream. The unit of governance is the lifecycle event, not the data volume.
- **A compliance-certified reporting system.** The REMIT-shaped export (v2) is an integration aid, clearly labelled non-certified. Glasshouse is not an ARM, an RRM, or a substitute for regulatory advice.
- **A multi-commodity ETRM (initially).** European power first; product packs may extend it later.
- **Advice.** Of any kind.

## Production readiness

Adopters should know where they stand. The readiness ladder (DESIGN.md section 9) runs from L0 (local evaluation) to L5 (supported enterprise deployment); the hosted demo is L0 and says so on screen. The designed adoption path is L2: run Glasshouse as a **shadow book** alongside your spreadsheets or incumbent, reconciled daily, until it has earned the right to be the book of record.

## TimescaleDB licensing note

Glasshouse depends on PostgreSQL with the TimescaleDB extension. The community (TSL-licensed) features it uses (compression, continuous aggregates) are free to self-host and free to use when running your own product; they are not licensed for offering TimescaleDB itself as a managed service. Some managed Postgres providers offer only the Apache-2 subset or no extension at all; adopters on such hosts can run the `timescale/timescaledb` image or install the extension themselves.
