# Vendored assets

OpenHealthAtlas uses no CDN because its CSP is self-only and private deployments should
not leak page loads to an unrelated asset host.

| File | Version | Source | sha256 |
|---|---|---|---|
| echarts.min.js | 5.6.0 | https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js | bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1 |
| body-muscles/body-muscles.umd.min.js | 1.0.0 | https://registry.npmjs.org/body-muscles/-/body-muscles-1.0.0.tgz (`dist/umd/`) | c7fe91ea3ea05a11f3b684c165af5b7be085d88bd8da9dcc1230e1f181613604 |

To upgrade: download the new pinned version, verify the hash from a second source,
update this table, and test every chart page.

## Apache ECharts — license and provenance

Apache-2.0, Copyright 2017-2024 The Apache Software Foundation. The vendored
5.6.0 distribution file is unmodified. The upstream `LICENSE`, `NOTICE`, and
embedded d3 BSD-3-Clause license from the exact `5.6.0` tag are included under
`echarts/`. Official source: <https://github.com/apache/echarts/tree/5.6.0>.

## body-muscles — license & provenance

Apache-2.0, © 2024 Ivan Vulović (`vulovix`). `LICENSE` and `NOTICE` are vendored
alongside the file in `body-muscles/`, copied verbatim from the npm package.
Review notes: full source read (zero dependencies, no network
calls, no eval, no install scripts), and the npm tarball's `dist/` reproduced
**byte-identical** from the GitHub source (github.com/vulovix/body-muscles,
commit `15c8085e`) with pinned typescript+esbuild. The vendored file is UNMODIFIED —
re-verify against the sha256 above; we consume only its exported SVG path data
(`window.BodyMuscles`), not its `BodyChart` renderer (see `js/muscle-figure.js`).
