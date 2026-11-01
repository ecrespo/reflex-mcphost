# Third-party notice — test fixtures only

These files are **test fixtures**. They live under `tests/` and are never part of the
`reflex-mcphost` wheel (checked by `scripts/check_wheel.py`).

## `basic-vanillajs-1.0.1.mcp-app.html`

- **Source:** npm package `@modelcontextprotocol/server-basic-vanillajs@1.0.1`, file
  `package/dist/mcp-app.html` (a single-file Vite build of the "Get Time" example App),
  extracted unmodified.
- **Upstream repository:** <https://github.com/modelcontextprotocol/ext-apps>, directory
  `examples/basic-server-vanillajs` (tag `v1.0.1`, the MCP Apps `2026-01-26` era).
- **Tarball:** `https://registry.npmjs.org/@modelcontextprotocol/server-basic-vanillajs/-/server-basic-vanillajs-1.0.1.tgz`,
  npm shasum `b693334d2febcbaf8377dd8f48eab5009312dcfa` (verified 2026-11-01).
- **File:** 390,445 bytes, sha256
  `076c3926f3fbf6e81f7cd8b9ebf428f1c6d9b0a37f9a4e4cf0c2f0e9aaff923c` (asserted by
  `tests/integration/apps/test_bridge_fixture.py`; matches spike S-3).
- **Licence:** MIT — the package's `package.json` declares `"license": "MIT"` (checked
  2026-11-01), as does the `ext-apps` repository. Copyright © the Model Context Protocol
  contributors (Anthropic, PBC and others).

The MIT licence permits use, copying and redistribution provided the copyright notice and
permission notice are included:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this
> software and associated documentation files (the "Software"), to deal in the Software
> without restriction, including without limitation the rights to use, copy, modify, merge,
> publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
> to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
> INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
> PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
> FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
> OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
> DEALINGS IN THE SOFTWARE.

## `probe.html`

Hand-written for this project (MIT, like the project). A dependency-free View that speaks
the View side of MCP Apps `2026-01-26` and exposes hooks for the negative paths (see the
comment at the top of the file).
