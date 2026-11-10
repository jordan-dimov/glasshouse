# Glasshouse

Glasshouse is an open-source system of record for power trading. It keeps a firm's trades, price curves, positions, and profit and loss in one place, with a complete history of every change.

**See it running: [glasshouse.a115.co.uk](https://glasshouse.a115.co.uk).** A small demo trading book: its trades, hourly positions, valuations, a corrected price curve, and the history behind them. The login is shared, so [open an issue](https://github.com/jordan-dimov/glasshouse/issues/new) to ask for the password, or run it yourself in a few minutes (below).

## Why it exists

A small power trading team, or a company running batteries or renewables, has two choices today. Spreadsheets and a home-made trade table, which nobody can audit. Or a large closed vendor system, which costs more than the team does.

Glasshouse is the missing middle: a book of record you can read, run and check yourself.

## What makes it different

- **Nothing is overwritten.** A corrected curve or an amended trade is a new version beside the old one. The old figure, and every valuation made from it, stays on the record.
- **Bad changes are refused, with a reason.** The rules live in [one readable file](src/glasshouse/commit/glasshouse.morph). A change that breaks one is not saved, and the refusal names the rule: this trader may not trade this book, this trade would take the book over its limit in this hour.
- **Every valuation shows its sources.** It records the exact curve version and trade terms it was computed from, so last week's number can be explained this week.
- **You can check the records yourself.** One command confirms the database still agrees with its own history. Another exports that history for an outsider to verify offline.

## Where it stands

Early. It handles one product, fixed-price power, from end to end: CSV imports, hourly positions, valuations, corrections, amendments, position limits, a web interface and an HTTP API.

It is ready to evaluate, not yet to run a desk on. [SCOPE.md](SCOPE.md) says plainly what it is not.

## Where it is going

None of this exists yet. It is the road ahead.

- **View any past moment.** The history is complete already. Next is seeing the whole system as it stood at a chosen time.
- **Run beside what you have.** Import your spreadsheet's or current system's exports, compare daily, and see exactly where the two differ.
- **More of the lifecycle.** Counterparty confirmations, settlement, and products such as power purchase agreements and battery storage.
- **A safe place for software to trade.** Every change is a proposal the rules accept or refuse, whoever makes it. Algorithms and AI agents can propose trades, cannot break the book, and leave a full record of what they did.

[DESIGN.md](DESIGN.md) has the full plan and the reasoning.

## Run it locally

You need Python 3.13 or later, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
./scripts/install-morpholog.sh ~/.local/bin
export GLASSHOUSE_MORPHOLOG_BIN=~/.local/bin/morpholog
docker compose up -d
uv sync
uv run python -m glasshouse.cli seed --reset
uv run uvicorn glasshouse.api.app:app
```

Then open <http://localhost:8000/ui>. The first command downloads [Morpholog](https://github.com/jordan-dimov/morpholog), the open-source engine underneath that enforces the rules and keeps the history. The fifth loads the demo book.

Tests, the command-line tools, configuration and deployment are in [docs/development.md](docs/development.md).

## Licence and contact

Apache 2.0, see [LICENSE](LICENSE). Questions, ideas and contributions are welcome through [GitHub issues](https://github.com/jordan-dimov/glasshouse/issues).
