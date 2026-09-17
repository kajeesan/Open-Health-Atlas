# Third-party notices

Open Health Atlas original project code in this revision is licensed under
the GNU Affero General Public License, version 3 only (AGPL-3.0-only). The
components below retain their own copyright, license, notice, and attribution
requirements.

## Apache ECharts 5.6.0

- Component: `app/static/vendor/echarts.min.js`
- Official project: <https://github.com/apache/echarts>
- Exact release: <https://github.com/apache/echarts/releases/tag/5.6.0>
- License: Apache License 2.0 with upstream subcomponent terms
- SHA-256: `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1`
- Distribution state: unmodified minified release bundle

The exact upstream 5.6.0 files are included at:

- `app/static/vendor/echarts/LICENSE`
- `app/static/vendor/echarts/NOTICE`
- `app/static/vendor/echarts/licenses/LICENSE-d3`

The d3-derived subcomponents identified by upstream retain their BSD
3-Clause terms. Preserve all three files when redistributing the ECharts bundle.

## body-muscles 1.0.0

- Component: `app/static/vendor/body-muscles/body-muscles.umd.min.js`
- Official project: <https://github.com/vulovix/body-muscles>
- License: Apache License 2.0
- Copyright: 2024 Ivan Vulović
- SHA-256: `c7fe91ea3ea05a11f3b684c165af5b7be085d88bd8da9dcc1230e1f181613604`
- Distribution state: unmodified `dist/umd` bundle

The package's `LICENSE` and `NOTICE` are included alongside the bundle.
OpenHealthAtlas uses its exported SVG path data through
`app/static/js/muscle-figure.js`.

## Python dependencies

Python packages listed in `requirements.txt`, `requirements-dev.txt` and the
optional `requirements-mcp.txt` are
installed dependencies rather than copied source. Their upstream terms still
apply to users and distributors. Recheck their exact versions, licenses, and
security advisories before each public release.

The optional local MCP server uses the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), pinned in
`requirements-mcp.txt` and licensed under the MIT License. It is not installed
by the ordinary panel/runtime requirements, and no SDK source is vendored here.

## Contributor Covenant

`CODE_OF_CONDUCT.md` adapts Contributor Covenant 2.0, licensed under
Creative Commons Attribution 4.0 International. Its source attribution,
license link and description of the reporting-section adaptation are retained
in that document. This documentation license does not change the
AGPL-3.0-only license for original project code.
