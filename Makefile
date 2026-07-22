.PHONY: install dev test lint docker-build docker-up docker-down smoke

install:
	python -m pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test:
	pytest -q

lint:
	ruff check .

smoke:
	python scripts/smoke_test.py

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down
