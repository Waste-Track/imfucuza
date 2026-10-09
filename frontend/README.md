# Frontend

Household and rider PWAs. The API contract is the backend's OpenAPI schema at `/openapi.json`.

## What CI expects

- A `package.json` in this folder, plus a lockfile: `package-lock.json`, `pnpm-lock.yaml` or `yarn.lock`.
- Any of the scripts `lint`, `typecheck`, `test` and `build`. CI runs the ones that exist, in that order.
- Optional: a `.nvmrc` with the Node version. Default is Node 24.

Until `package.json` exists, CI skips this folder.
