# Elevate API

Independent FastAPI service for Elevate scoring and client-facing APIs.

## Local checks

The host does not need a global `uv` installation:

```bash
docker run --rm -v "$PWD/apps/api:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.14-bookworm uv run pytest

docker run --rm -v "$PWD/apps/api:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.14-bookworm uv run ruff check .
```

## Full local stack

Install Docker and the Supabase CLI, then run from the repository root:

```bash
make elevate-up
```

This starts Supabase and the merged Open Wearables plus Elevate Compose stack in the foreground. The Elevate API is available at `http://localhost:8100`; its health endpoint is `http://localhost:8100/health`.

For detached operation use `make elevate-run`, and stop both stacks with `make elevate-down`.

## Local authentication

With the full stack running, choose a local-only password and create or update the two test users:

```bash
export ELEVATE_TEST_USER_PASSWORD='choose-a-local-password'
make elevate-auth-users
```

This prepares `test-user-1@elevate.local` and `test-user-2@elevate.local`. The password is read from the environment and is not stored in the repository.

Open `http://localhost:8100/docs` to use the Swagger UI. The `GET /me` endpoint accepts a Supabase access token through its Bearer authentication control.

The Bruno collection performs a real password login, keeps the access token in memory, and verifies `GET /me`:

```bash
make elevate-auth-test
```

The command reads the local publishable key from `supabase status`; no key or token is committed.
