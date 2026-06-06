"""Product packs: where product knowledge lives.

The core knows about proposals, transitions, payloads, projections, books,
counterparties, authority and time, and nothing about power periods, PPA
structures or BESS legs. A product pack owns the vocabulary, capture
fields, delivery-period expansion, valuation inputs, projection folds,
import formats and evidence templates for one product family.

v0 ships exactly one pack (power_fixed_price), in-tree, with the boundary
observed but no plugin machinery; the pack interface is extracted when the
second pack forces it.
"""
