# Retired Elevate API Prototype

This prototype is no longer an active service. ELEVATE-owned server behavior now lives in the sibling `elevate-fitness` repository under `apps/web/app/api/member/wearables`.

The files remain temporarily to preserve local work. Do not deploy, extend, or reactivate this service from the Open Wearables repository.

## Local checks

The host does not need a global `uv` installation:

```bash
docker run --rm -v "$PWD/apps/api:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.14-bookworm uv run pytest

docker run --rm -v "$PWD/apps/api:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.14-bookworm uv run ruff check .
```

These checks are retained only for reviewing the archived prototype.
