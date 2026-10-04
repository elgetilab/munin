# Munin chat UI

React + TypeScript + Vite. The chat, the knowledge browser, settings and the
admin panel, served by Caddy as static files from `frontend/static/chat/`.

```bash
npm ci
npm run dev        # http://localhost:5173; /api goes to 127.0.0.1:18080 (vite.config.ts)
npm test           # vitest unit tests
npm run e2e        # Playwright against a production build, backend mocked (e2e/)
npm run build      # writes ../static/chat/ (emptied first)
```

Deployments do not need Node on the server: the `webui` compose profile builds
the same bundle in a container (`Dockerfile`).

**Site URLs.** `src/lib/urls.ts` derives the auth service and the sibling sites
at runtime from the host the UI is served on: on `chat.lab.example.edu` it
calls `auth.lab.example.edu`. Nothing is baked in, so one build works on any
domain that follows the subdomain layout. Layouts that do not (the single-host
`http://localhost` setup) set `VITE_*` overrides; see `.env.example`.

The API itself is same-origin (`/api`, `/paper`) and needs no configuration.
The backend contract it follows is
[`shared/docs/BACKEND-API.md`](../../shared/docs/BACKEND-API.md).
