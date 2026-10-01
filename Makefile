DOCKER_COMMAND = docker compose -f docker-compose.yml
DOCKER_EXEC = $(DOCKER_COMMAND) exec app
ALEMBIC_CMD = uv run alembic
ELEVATE_DOCKER_COMMAND = docker compose -f docker-compose.yml -f docker-compose.elevate.yml

help:	## Show this help.
	@echo "============================================================"
	@echo "This is a list of available commands for this project."
	@echo "============================================================"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

build:	## Builds docker image
	$(DOCKER_COMMAND) build --no-cache

run:	## Runs the environment in detached mode
	$(DOCKER_COMMAND) up -d --force-recreate
	$(DOCKER_COMMAND) rm -f db-svix-init

up:	## Runs the non-detached environment
	$(DOCKER_COMMAND) up --force-recreate

watch:	## Runs the environment with hot-reload
	$(DOCKER_COMMAND) watch

stop:	## Stops running instance
	$(DOCKER_COMMAND) stop

down:	## Kills running instance
	$(DOCKER_COMMAND) down

test:	## Run the tests.
	cd backend && uv run pytest -v --cov=app

migrate:  ## Apply all migrations
	$(DOCKER_EXEC) $(ALEMBIC_CMD) upgrade head

seed:  ## Seed sample data (test users and activity data)
	$(DOCKER_EXEC) uv sync --group dev
	$(DOCKER_EXEC) uv run python scripts/init/seed_activity_data.py

create_migration:  ## Create a new migration. Use 'make create_migration m="Description of the change"'
	@if [ -z "$(m)" ]; then \
		echo "Error: You must provide a migration description using 'm=\"Description\"'"; \
		exit 1; \
	fi
	$(DOCKER_EXEC) $(ALEMBIC_CMD) revision --autogenerate -m "$(m)"

downgrade:  ## Revert the last migration
	$(DOCKER_EXEC) $(ALEMBIC_CMD) downgrade -1

reset_db:  ## Truncate all tables in the database (WARNING: deletes all data)
	$(DOCKER_EXEC) uv run python scripts/reset_database.py

elevate-up:  ## Start Supabase and the full Open Wearables + Elevate stack in the foreground
	./scripts/elevate-dev.sh

elevate-run:  ## Start Supabase and the full Open Wearables + Elevate stack in detached mode
	supabase start
	$(ELEVATE_DOCKER_COMMAND) up -d --build

elevate-down:  ## Stop the full Open Wearables + Elevate stack and Supabase
	$(ELEVATE_DOCKER_COMMAND) down
	supabase stop

elevate-test:  ## Run Elevate API tests
	docker run --rm -v "$(CURDIR)/apps/api:/app" -w /app ghcr.io/astral-sh/uv:python3.14-bookworm uv run pytest

elevate-lint:  ## Run Elevate API Ruff checks
	docker run --rm -v "$(CURDIR)/apps/api:/app" -w /app ghcr.io/astral-sh/uv:python3.14-bookworm uv run ruff check .
	docker run --rm -v "$(CURDIR)/apps/api:/app" -w /app ghcr.io/astral-sh/uv:python3.14-bookworm uv run ruff format --check .

elevate-auth-users:  ## Create/update local Elevate auth users (requires ELEVATE_TEST_USER_PASSWORD)
	python3 scripts/seed-elevate-auth-users.py

elevate-auth-test: elevate-auth-users  ## Test Supabase login and /me with Bruno
	@eval "$$(supabase status -o env 2>/dev/null)"; \
	cd apps/api/bruno && npx --yes @usebruno/cli run . -r --env local \
		--env-var "supabase_key=$$PUBLISHABLE_KEY" \
		--env-var "test_password=$$ELEVATE_TEST_USER_PASSWORD" \
		--tests-only --bail
