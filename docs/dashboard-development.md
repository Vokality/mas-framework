# Dashboard development

The management frontend is a Svelte application in `dashboard/`. The broker
serves its built assets; installing the server wheel requires no Node runtime.
Use Node 24 and the committed npm lockfile when developing the frontend.

```sh
npm ci --prefix dashboard
npm run check --prefix dashboard
npm run format:check --prefix dashboard
npm run contracts:check --prefix dashboard
npm test --prefix dashboard
npm run build --prefix dashboard
uv run --no-project python tools/check_dashboard_bundle.py --rebuild
```

Commit the frontend source and these generated files together:

- `packages/mas-server/src/mas_server/dashboard_assets/index.html`
- `packages/mas-server/src/mas_server/dashboard_assets/assets/dashboard.js`
- `packages/mas-server/src/mas_server/dashboard_assets/assets/dashboard.css`

The verification command rebuilds and compares the bundle with its previous
bytes. It fails if the source and bundled assets differ. Validation, integration,
production acceptance and release workflows all perform this check. The release
workflow also checks that the server wheel contains the identical bundle.

The validation workflow also runs Chromium browser regressions. To run them
locally:

```sh
uv run python -m dashboard.tests.generate_browser_fixtures --check
cd dashboard
npx playwright install chromium
npm run test:browser
```

For development, start the local control room and Vite in separate terminals:

```sh
uv run examples/control_room.py
npm run dev --prefix dashboard
```

Vite proxies `/api` and `/healthz` to the local broker. Reader credentials and
authorization remain the same as the production management API. The static
application shell contains no operational data; API reads require the configured
reader access. The UI provides no broker mutation endpoints.

Production uses exact page routes: `/overview`, `/fleet`, `/performance`,
`/traces`, `/traces/{32-lowercase-hex-id}`, `/alerts`, `/agents`, `/queues`,
`/activity` and `/telemetry`. `/` opens the application. Unknown routes, missing
assets and unknown API paths return 404.

The broker loads the three packaged files in a worker during startup and serves
the cached bytes without request-time filesystem access. Restart the broker
after a rebuild. Assets use `no-store` because their filenames are stable. The
content security policy permits same-origin scripts and styles, and inline
style attributes for Svelte chart positioning; it does not permit inline script
execution or runtime code compilation.

To verify a locally built wheel:

```sh
uv build --package mas-server --no-sources
uv run --no-project python tools/check_dashboard_bundle.py --wheel dist/mas_server-*.whl
```
