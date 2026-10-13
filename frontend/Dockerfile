# syntax=docker/dockerfile:1
# Next.js frontend (public entry point). Build context: frontend/ (see ../compose.yaml).
#   target "dev"  — node_modules in the image, source bind-mounted, next dev (compose.dev.yaml)
#   target "prod" — `next build` standalone output, non-root (compose.yaml)
FROM node:24-alpine AS deps
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

FROM deps AS dev
EXPOSE 5173
CMD ["node", "node_modules/next/dist/bin/next", "dev", "--webpack", "-H", "0.0.0.0", "-p", "5173"]

FROM deps AS build
COPY . .
RUN npm run build

FROM node:24-alpine AS prod
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=5173
WORKDIR /app
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 5173
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 \
    CMD wget -q -O /dev/null http://127.0.0.1:5173/chat || exit 1
CMD ["node", "server.js"]
