.PHONY: up down logs build migrate revision import scrape refresh shell test fmt

up:
	docker compose up -d --build
	$(MAKE) migrate

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

build:
	docker compose build

migrate:
	docker compose run --rm api alembic upgrade head

revision:
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

# make import FILE=groups.txt
import:
	docker compose run --rm api python -m app.cli import-groups $(FILE)

# make scrape (enqueues all active groups for scraping)
scrape:
	docker compose run --rm api python -m app.cli enqueue-scrape

# make refresh (enqueues metrics refresh for fresh + stale posts)
refresh:
	docker compose run --rm api python -m app.cli enqueue-refresh

shell:
	docker compose exec api python

psql:
	docker compose exec postgres psql -U vkstat -d vkstat

test:
	docker compose run --rm api pytest -q
