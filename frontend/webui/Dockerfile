# ==============================================================================
# MUNIN CHAT UI - build image
# ==============================================================================
# Builds the React chat app so a single-host `docker compose up` needs no Node
# toolchain on the host. The VPS deploy does NOT use this: there the UI is built
# with `npm run build` and rsynced, and that path is unchanged.
#
# The site URLs are BUILD-TIME values (Vite inlines `import.meta.env.VITE_*`),
# so they arrive as build args rather than runtime env. Leaving them unset
# produces exactly the production bundle, because src/lib/urls.ts defaults every
# one of them to the live deployment.
#
# The final stage is not a server. It holds the built assets and copies them
# into a mounted directory on run, because Caddy serves the static tree from the
# host filesystem and expects the chat build to sit alongside the other UIs.
# ==============================================================================

FROM node:22-alpine AS build

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
FROM alpine:3.20

COPY --from=build /dist /dist

# Publish into the mounted output directory. `rm` first so a stale bundle from
# an earlier build cannot linger: Vite emits content-hashed filenames, so old
# ones are never overwritten, only orphaned.
CMD ["sh", "-c", "rm -rf /out/* && cp -a /dist/. /out/ && echo 'chat UI published to /out'"]
