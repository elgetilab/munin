# ==============================================================================
# MUNIN CHAT UI - build image
# ==============================================================================
# Builds the React chat app so an install needs no Node toolchain on the host
# (the `webui` profile; configure.sh enables it). The reference VPS builds with
# `npm run build` on a workstation and rsyncs instead.
#
# Site URLs: leave the VITE_* build args unset and src/lib/urls.ts derives them
# at runtime from the host the UI is served on, so one bundle fits any domain.
# Only layouts that do not follow the subdomain pattern (the one-machine
# http://localhost setup) pass them.
#
# The final stage is not a server. It holds the built assets and copies them
# into a mounted directory on run, because Caddy serves the static tree from the
# host filesystem and expects the chat build to sit alongside the other UIs.
# ==============================================================================

FROM node:22.23.3-alpine AS build

WORKDIR /app

# Dependencies first, so a source-only change does not re-resolve the tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Only VITE_-prefixed vars reach the bundle; Vite ignores anything else.
ARG VITE_AUTH_BASE
ARG VITE_SITE_HOME
ARG VITE_CHAT_URL
ARG VITE_SEARCH_URL
ARG VITE_DOCS_URL
ARG VITE_UPLOAD_URL
ARG VITE_API_PUBLIC_URL

# --outDir overrides vite.config.ts's `../static/chat`, which is a host-relative
# path that means nothing inside the image.
RUN npm run build -- --outDir /dist --emptyOutDir

# ==============================================================================
FROM alpine:3.20.10

COPY --from=build /dist /dist

# Publish into the mounted output directory. `rm` first so a stale bundle from
# an earlier build cannot linger: Vite emits content-hashed filenames, so old
# ones are never overwritten, only orphaned.
CMD ["sh", "-c", "rm -rf /out/* && cp -a /dist/. /out/ && echo 'chat UI published to /out'"]
