# Deploying to Lite

How the web UI, retrieval, auth, static pages and Caddy config reach the Lite server.
Neither server folder is a git checkout, so nothing here is a `git pull`. Login details
are not in this public repo.

## Before any deploy

- **Compare first.** Hash the server's files against the commit you think is live, and
  stop if they differ, because someone else may have published.
- **Read the live bundle** from `~/munin/frontend/static/chat/index.html` immediately
  before publishing, and stop if it is not the one you expect. Other work publishes to
  the same folder.
- **Check nobody is using it.** Retrieval restarts drop chats in progress:
  `docker logs --since 3m munin-llm-claude | grep 'usage model'`. For auth, look for
  `POST /login`, `POST /verify` and `OTP email` in `frontend-munin-auth-1`.
- **Back up** the files you will replace, and `docker tag` the running image, under a
  dated name.
- **Export cleanly from Windows:** always `git -c core.autocrlf=false archive`, and the
  same for `git show`, or the files reach the server with CRLF line endings.

## Web UI

Build from the export with `npm ci` and no `VITE_*` variables set, then copy into
`~/munin/frontend/static/chat`.

1. Copy `assets/` first and keep the old assets, because open tabs still need them.
2. Copy `sw.js`, `manifest.json` and `demo/demo.json`.
3. Copy `index.html` last, by rename.

Caddy reads this from disk, so there is no restart. To roll back, put back the previous
`index.html`.

## Static pages

Copy changed files into `~/munin/frontend/static`, with `shared/` first. Copy them raw,
because Caddy fills in their templates as it serves them. No restart.

## Retrieval

The code is baked into the image. Upload changed files to
`~/munin-lite/backend/retrieval`, then from `~/munin-lite`, whose `.env` sets
`COMPOSE_FILE` and the passwords:

```
docker compose build retrieval
docker compose up -d --no-deps --no-build retrieval
```

It is healthy again in about 30 seconds. Run its script-style tests in the container:
`docker exec munin-retrieval python /app/tests/<test>.py`.

## Auth

The code and templates are baked into the image. Upload to `~/munin/frontend/auth`, then
from `~/munin/frontend`:

```
docker compose build munin-auth
docker compose up -d --no-deps --no-build munin-auth
```

It is back in about 3 seconds with sessions kept. Codes in flight and rate limits are
lost. Check `AUTH_DEV_ECHO_OTP` is still empty in the container.

## Caddy

`~/munin/frontend/caddy` is mounted as a folder, so replace `Caddyfile` by rename.
Validate a config before installing it, and then reload:

```
docker exec -i frontend-caddy-1 caddy adapt --adapter caddyfile --config /dev/stdin < Caddyfile > /dev/null
docker exec frontend-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Send the `adapt` output to `/dev/null`, because it prints the expanded config, including
the gateway token.

## Order

Deploy the backend before a web UI that calls a new route, then Caddy, then the web UI.

## After a deploy

- Signed-out requests to chat, search, research, docs and upload should return 302 with
  `Cache-Control: no-cache`. Most of those pages sit behind login, so a status check
  alone proves little.
- Check for errors in the logs of each service you touched.
- Run the live checks, with a person signing in by hand. Nothing here sends the login
  mail itself, and never send one without asking.

  ```
  cd frontend/webui
  export MUNIN_LIVE_STATE=<a file outside the repo>
  npx playwright test -c playwright.live.config.ts --project=login --headed
  npx playwright test -c playwright.live.config.ts --project=live
  ```

  The saved session file works like a password, and failure output can print the cookie.
  The last check signs that session out, so confirm the old cookie gets a 401, then
  delete the file.
