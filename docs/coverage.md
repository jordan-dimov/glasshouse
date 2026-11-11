# Coverage: what a full ETRM does, and where Glasshouse stands

An ETRM (energy trading and risk management system) is the system a trading desk runs its book on. This page lists what a full one does and says, for each function, where Glasshouse stands today. It is kept honest in the same way as [SCOPE.md](../SCOPE.md): nothing here is a promise of a date.

The four states:

- **Has**: it works on the main branch today. A note says where it is narrow.
- **Building**: work is under way now.
- **Planned**: an issue exists. Nothing more is claimed.
- **Will not do**: left out on purpose, with the reason.

Every new function arrives one of three ways: as rules in [the one rule file](../src/glasshouse/commit/glasshouse.morph), as facts in the ledger, or as a product pack. A desk should never need to fork the code to make Glasshouse fit.

## Trades and orders

| Function | State | Notes |
|---|---|---|
| Capture a fixed-price power trade | Has | By CSV import, command line or agent. Whole hours only. |
| Amend a trade, keeping the old version | Has | A new version beside the old one; nothing is overwritten. |
| Enter a trade on the screen | Planned | [#119](https://github.com/jordan-dimov/glasshouse/issues/119) |
| Open orders that hold room under a limit | Has | Called instructions. Placed and cancelled by agents or the API. |
| Approval of an order by a second person | Has | The approver must hold the right and must not be whoever placed it. Recorded, not enforced on the market. |
| Recording fills from the market, and breaches | Planned | [#114](https://github.com/jordan-dimov/glasshouse/issues/114). Waits for the next release of the rules engine. |
| Delivery periods of 15 and 30 minutes | Planned | [#116](https://github.com/jordan-dimov/glasshouse/issues/116). Needed for Great Britain and for intraday. Waits for the next release of the rules engine. |
| Labels on trades (strategy, desk, customer) | Planned | [#128](https://github.com/jordan-dimov/glasshouse/issues/128) |
| Counterparties with legal identity and agreements | Planned | [#117](https://github.com/jordan-dimov/glasshouse/issues/117). A counterparty is a bare name today. |
| Trades in from an exchange's or broker's own file | Planned | [#127](https://github.com/jordan-dimov/glasshouse/issues/127). Waiting for a real file to work from. |

## Prices

| Function | State | Notes |
|---|---|---|
| Price curves with versions and corrections | Has | Hourly. A correction adds a version; the old one and every valuation made from it are kept. |
| Each market priced in its own currency | Has | EUR, GBP, NOK, SEK, DKK, PLN, RON. Never added together. |
| A business date, so a curve can be registered every day | Planned | [#44](https://github.com/jordan-dimov/glasshouse/issues/44). Until then a second day's curve stops valuations in that market. |
| Published index prices (day-ahead and the like) | Planned | Part of [#124](https://github.com/jordan-dimov/glasshouse/issues/124). |
| Live market data feeds | Will not do | A market data platform is its own product. Prices arrive as files or through the API. |

## Position and value

| Function | State | Notes |
|---|---|---|
| Position per book and delivery hour | Has | |
| Valuation against the curve, naming what it used | Has | Every value records the exact curve version and trade version. |
| The book as it stood at a past moment | Has | Trades, positions and valuations. Other screens: [#41](https://github.com/jordan-dimov/glasshouse/issues/41). |
| Explain any number back to its sources | Planned | [#121](https://github.com/jordan-dimov/glasshouse/issues/121) |
| Fees and costs, so value is net | Planned | [#122](https://github.com/jordan-dimov/glasshouse/issues/122) |
| Realised P&L | Planned | [#123](https://github.com/jordan-dimov/glasshouse/issues/123) |
| Figures out to Excel | Planned | [#118](https://github.com/jordan-dimov/glasshouse/issues/118). The JSON API exists today. |

## Limits and risk

| Function | State | Notes |
|---|---|---|
| A position limit per book and hour, enforced before a trade is recorded | Has | The refusal names the rule, the hour and the figures. |
| Open orders counted against the limit | Has | Buys and sells are never set against each other. |
| More kinds of limit, switched on by a desk | Planned | [#129](https://github.com/jordan-dimov/glasshouse/issues/129): trade size, market, notional value, labels. |
| A limit on exposure to one counterparty | Planned | Part of [#117](https://github.com/jordan-dimov/glasshouse/issues/117). |
| Value-at-risk, stress tests | Will not do | A risk engine is its own product. Glasshouse can feed one. |
| A credit engine with collateral and netting | Will not do | Its own product. |

## From trade to cash

| Function | State | Notes |
|---|---|---|
| Confirmations to counterparties | Planned | [#81](https://github.com/jordan-dimov/glasshouse/issues/81) |
| A feed of events for other systems | Planned | [#81](https://github.com/jordan-dimov/glasshouse/issues/81) |
| A settlement statement to check an invoice against | Planned | [#123](https://github.com/jordan-dimov/glasshouse/issues/123) |
| Invoicing, billing and payments | Will not do | A billing system is its own product. |
| Accounting postings | Will not do | Belongs in the accounting system. |
| Scheduling and nominations | Will not do | Its own product, and specific to each grid operator. |
| Imbalance settlement | Will not do, for now | Would belong to a product pack that somebody must first ask for. |
| A regulatory export, labelled not certified | Planned | [#130](https://github.com/jordan-dimov/glasshouse/issues/130). Glasshouse will not be a certified reporting system. |

## Products

| Function | State | Notes |
|---|---|---|
| Fixed-price physical power | Has | The one product today. |
| A price tied to an index | Planned | [#124](https://github.com/jordan-dimov/glasshouse/issues/124) |
| PPAs paid as produced | Planned | [#125](https://github.com/jordan-dimov/glasshouse/issues/125) |
| Trades linked to a battery | Planned | [#126](https://github.com/jordan-dimov/glasshouse/issues/126). No optimisation. |
| Gas and other commodities | Will not do, for now | European power first. A pack could add one if somebody asks. |
| Options and structured products | Will not do, for now | Not asked for by the desks this is built for. |
| Optimisation and forecasting | Will not do | Glasshouse records and checks what an optimiser decided. |

## Getting started beside what you have

| Function | State | Notes |
|---|---|---|
| CSV import with a preview and a reason for every refused row | Has | Trades and curves. A sample file is built in. |
| Reconcile daily against your spreadsheet or current system | Planned | [#120](https://github.com/jordan-dimov/glasshouse/issues/120) |
| An HTTP API | Has | Reads, a dry run, and the import. |
| Run it yourself | Has | Open source, Apache 2.0, one PostgreSQL database. |

## Control and evidence

| Function | State | Notes |
|---|---|---|
| Rules in one readable file, enforced outside the application | Has | |
| Permissions as recorded facts | Has | Granting is recorded like a trade. |
| Withdrawing a permission | Planned | [#115](https://github.com/jordan-dimov/glasshouse/issues/115). A permission cannot be taken back today. |
| A complete history of accepted changes | Has | Nothing can be edited or removed. |
| A list of refused changes | Has | Recent, and not guaranteed complete. Refusals are kept outside the checked record by design. |
| Check the record against its own history | Has | One button or one command. |
| Export the record for an outsider to verify offline | Has | For the whole ledger. One trade only: [#83](https://github.com/jordan-dimov/glasshouse/issues/83). |
| A published rule book, generated from the rules in force | Planned | [#86](https://github.com/jordan-dimov/glasshouse/issues/86) |
| Checkpoints countersigned by an outside timestamp authority | Planned | [#80](https://github.com/jordan-dimov/glasshouse/issues/80), then signatures: [#42](https://github.com/jordan-dimov/glasshouse/issues/42). |
| Sign-in for named users | Planned | Shared demo login for the screens; a token per actor for agents. What a database login may assert is [#85](https://github.com/jordan-dimov/glasshouse/issues/85). |

## Software that trades

| Function | State | Notes |
|---|---|---|
| An AI agent reads the book | Has | Through MCP. See [mcp.md](mcp.md). |
| An agent proposes trades and places orders, under the same rules as a person | Has | Refusals come back as data. |
| Each agent under its own name | Has | A token per actor. A token does not prove who holds it: [#85](https://github.com/jordan-dimov/glasshouse/issues/85). |

## The order of work

No dates. This is the order things are meant to be built in, and it can change.

1. Move to the next release of the rules engine, which lets one act branch. Two things below wait for it.
2. Fills and breaches ([#114](https://github.com/jordan-dimov/glasshouse/issues/114)) and shorter delivery periods ([#116](https://github.com/jordan-dimov/glasshouse/issues/116)).
3. The business date ([#44](https://github.com/jordan-dimov/glasshouse/issues/44)), because it blocks ordinary daily use.
4. What else changes the shape of the record, while no ledger is being kept: counterparties as facts ([#117](https://github.com/jordan-dimov/glasshouse/issues/117)). Withdrawing a permission ([#115](https://github.com/jordan-dimov/glasshouse/issues/115)) belongs here too.
5. What lets a desk start: figures out to Excel ([#118](https://github.com/jordan-dimov/glasshouse/issues/118)), a trade ticket ([#119](https://github.com/jordan-dimov/glasshouse/issues/119)), running beside what you have ([#120](https://github.com/jordan-dimov/glasshouse/issues/120)).
6. From trade to cash: fees ([#122](https://github.com/jordan-dimov/glasshouse/issues/122)), the settlement statement ([#123](https://github.com/jordan-dimov/glasshouse/issues/123)), then the product packs ([#124](https://github.com/jordan-dimov/glasshouse/issues/124), [#125](https://github.com/jordan-dimov/glasshouse/issues/125), [#126](https://github.com/jordan-dimov/glasshouse/issues/126)).
7. Fitting each desk: labels ([#128](https://github.com/jordan-dimov/glasshouse/issues/128)), more kinds of limit ([#129](https://github.com/jordan-dimov/glasshouse/issues/129)), the regulatory export ([#130](https://github.com/jordan-dimov/glasshouse/issues/130)).

The evidence work ([#121](https://github.com/jordan-dimov/glasshouse/issues/121), [#86](https://github.com/jordan-dimov/glasshouse/issues/86), [#41](https://github.com/jordan-dimov/glasshouse/issues/41), [#80](https://github.com/jordan-dimov/glasshouse/issues/80), [#83](https://github.com/jordan-dimov/glasshouse/issues/83)) runs alongside.

## Keeping this page true

A change that alters what Glasshouse does updates this page in the same pull request. A test checks the page's shape: every row has one of the four states, and every "Planned" or "Building" row links to an issue.
