# Vendored assets

Committed byte-exact; never served from a CDN (the UI is self-contained
by design). To upgrade: replace the file from the pinned upstream
release, update the hash and version here, and re-run the UI tests
(`tests/web/test_ui_pure.py` checks the file against this record).

## htmx.min.js

- Version: 4.0.0
- Source: https://unpkg.com/htmx.org@4.0.0/dist/htmx.min.js
  (release https://github.com/bigskysoftware/htmx/releases/tag/v4.0.0;
  npm carries 4.x under the `next` tag until early 2027 while 2.x stays
  `latest`, so only the version-pinned URL is correct)
- SHA-256: e484d9171a9db30a39c8f16e3d709d4137f3211c659f8e6125816635033d593f
- Licence: 0BSD (HTMX-LICENSE.txt alongside; the text is unchanged
  from 2.0.9)

### Upgrade record: 2.0.9 to 4.0.0 (28/08/2026)

htmx's own `upgrade-check` scanned the templates clean: every element
that swaps carries its own `hx-target`/`hx-swap`/`hx-push-url`, so
the move to explicit inheritance changed no markup, and there are no
event listeners, no extensions and no `hx-on`. What the routes and
templates now rely on, each a documented htmx 4 behaviour:

- `HX-Request-Type: partial` is the fragment discriminator, not
  `HX-Request`. A history restore is an htmx request too, targets the
  body (`HX-Request-Type: full`) and no longer comes from a local
  cache, so every back navigation asks the server for the page. Those
  responses carry `Vary: HX-Request-Type`, or an HTTP cache could
  serve the restore the fragment it stored for a panel swap.
- Error responses swap (htmx 2 swallowed 4xx/5xx). The 503 face has a
  fragment form, so an unavailable read model is rendered where the
  operator is looking, never a whole page nested inside a panel.
- The 60 s default request timeout is switched off on the verify form
  alone (`hx-config`): a verify is long by nature and a request cut
  short would render nothing, which is the silence the swap rule
  above removes. Its button is disabled in flight and a pending note
  is shown; both use core attributes.
- The indicator stylesheet htmx would inject is disabled from the
  config meta in base.html; the two rules live in forms.css with the
  rest of the component layer.

Not adopted, deliberately: morph swaps (the swapped fragments hold no
state worth preserving), view transitions (decoration), `hx-boost`,
`hx-partial`, and every extension.
