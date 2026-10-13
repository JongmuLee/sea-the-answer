# syntax=docker/dockerfile:1
# Playwright runner for `docker compose --profile e2e ...`. The image version must match
# @playwright/test in package.json so the bundled browsers fit.
FROM mcr.microsoft.com/playwright:v1.62.1-noble
WORKDIR /e2e
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
CMD ["npx", "playwright", "test"]
