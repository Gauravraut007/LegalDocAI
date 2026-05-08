# Top-level Makefile for the Legal Document Intelligence Platform.
# Works on Linux/macOS and inside WSL/Git-Bash on Windows.

COMPOSE        ?= docker compose
PROJECT        ?= ldip
COMPOSE_FILES  := -f docker-compose.yml
PROD_FILES     := -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help up down restart logs ps build rebuild migrate makemigrations \
        createsuperuser shell-fastapi shell-django shell-celery shell-postgres \
        test test-fastapi test-django lint fmt fmt-check clean

help:
	@echo "Targets:"
	@echo "  up               Start all services (dev)"
	@echo "  down             Stop all services"
	@echo "  logs             Tail logs (all services)"
	@echo "  ps               List service status"
	@echo "  build / rebuild  Build images"
	@echo "  migrate          Run alembic upgrade head AND django migrate"
	@echo "  createsuperuser  Create Django superuser interactively"
	@echo "  shell-fastapi    Bash shell inside fastapi container"
	@echo "  shell-django     Bash shell inside django container"
	@echo "  shell-postgres   psql shell"
	@echo "  test             Run all tests"
	@echo "  lint             Run ruff + mypy"
	@echo "  fmt              Run ruff format + black"

up:
	$(COMPOSE) $(COMPOSE_FILES) up -d --build

down:
	$(COMPOSE) $(COMPOSE_FILES) down

restart:
	$(COMPOSE) $(COMPOSE_FILES) restart

logs:
	$(COMPOSE) $(COMPOSE_FILES) logs -f --tail=200

ps:
	$(COMPOSE) $(COMPOSE_FILES) ps

build:
	$(COMPOSE) $(COMPOSE_FILES) build

rebuild:
	$(COMPOSE) $(COMPOSE_FILES) build --no-cache

migrate:
	$(COMPOSE) $(COMPOSE_FILES) exec -T fastapi alembic upgrade head
	$(COMPOSE) $(COMPOSE_FILES) exec -T django python manage.py migrate --noinput

makemigrations:
	$(COMPOSE) $(COMPOSE_FILES) exec -T django python manage.py makemigrations

createsuperuser:
	$(COMPOSE) $(COMPOSE_FILES) exec django python manage.py createsuperuser

shell-fastapi:
	$(COMPOSE) $(COMPOSE_FILES) exec fastapi bash

shell-django:
	$(COMPOSE) $(COMPOSE_FILES) exec django bash

shell-celery:
	$(COMPOSE) $(COMPOSE_FILES) exec celery_worker bash

shell-postgres:
	$(COMPOSE) $(COMPOSE_FILES) exec postgres psql -U postgres

test: test-fastapi test-django

test-fastapi:
	$(COMPOSE) $(COMPOSE_FILES) exec -T fastapi pytest -q || true

test-django:
	$(COMPOSE) $(COMPOSE_FILES) exec -T django python manage.py test --noinput || true

lint:
	ruff check fastapi_service django_service
	mypy fastapi_service/app django_service/core django_service/common || true

fmt:
	ruff format fastapi_service django_service
	black fastapi_service django_service

fmt-check:
	ruff format --check fastapi_service django_service
	black --check fastapi_service django_service

clean:
	$(COMPOSE) $(COMPOSE_FILES) down -v
