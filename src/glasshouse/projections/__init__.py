"""The read side: projections and the projector.

Every table in the app schema is one of exactly two things: a projection
(derived state carrying the transition id it came from, rebuilt by
replaying the transition log from zero) or a hash-anchored payload (bulk
content whose hash was admitted in a governed claim). That law is what
makes ``glasshouse verify`` possible.

The projector is a library with three run modes chosen by config: inline
after each write, background thread, or separate worker. It tails the
transition log, not the outbox.
"""
