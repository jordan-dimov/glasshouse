"""The HTTP surface: FastAPI app, routes, and the server-rendered UI.

A write is a proposal; acceptance commits an event; rejection is a lawful,
documented outcome with a structured reason, not an error. Request models
are generated from the commit layer's schemas in a build step, committed,
and drift-checked in CI. Every UI screen renders a public API query.
"""
