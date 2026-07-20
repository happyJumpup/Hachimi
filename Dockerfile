# syntax=docker/dockerfile:1.7

FROM node:24.14.1-bookworm-slim AS web-builder

WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@11.9.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @hachimi/web...

COPY apps/web apps/web
RUN pnpm --filter @hachimi/web build

FROM ghcr.io/astral-sh/uv:0.11.7 AS uv-runtime

FROM python:3.12-slim-bookworm AS app-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/workspace/services/analysis-api/src \
    WEB_STATIC_ROOT=/workspace/apps/web/dist

COPY --from=uv-runtime /uv /uvx /bin/

WORKDIR /workspace
COPY services/analysis-api/pyproject.toml services/analysis-api/uv.lock services/analysis-api/
RUN uv sync --project services/analysis-api --frozen --no-dev --no-install-project

COPY services/analysis-api/src services/analysis-api/src
COPY skills skills
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY licenses licenses
COPY --from=web-builder /workspace/apps/web/dist apps/web/dist

RUN uv sync --project services/analysis-api --frozen --no-dev \
    && find /workspace/services/analysis-api/.venv/lib -type f -path '*/imageio_ffmpeg/binaries/ffmpeg-*' -delete \
    && test -z "$(find /workspace/services/analysis-api/.venv/lib -type f -path '*/imageio_ffmpeg/binaries/ffmpeg-*' -print -quit)" \
    && groupadd --system --gid 10001 hachimi \
    && useradd --system --uid 10001 --gid hachimi --home-dir /nonexistent --shell /usr/sbin/nologin hachimi \
    && mkdir -p /workspace/tmp/analysis-runs \
    && chown -R hachimi:hachimi /workspace/tmp

USER 10001:10001
EXPOSE 8000

CMD ["services/analysis-api/.venv/bin/uvicorn", "hakimi_analysis.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
