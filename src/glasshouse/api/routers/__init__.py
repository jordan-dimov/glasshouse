"""HTTP routers for the needle's read surface.

Each router takes the engine or the commit client through the
`Depends`-compatible accessors in `api.deps`; the app factory includes
them. Reads project the projection tables (law 4); `/explain` is a
read-only dry run through the commit layer.
"""
