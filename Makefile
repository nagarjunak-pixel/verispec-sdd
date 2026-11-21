.PHONY: sync lint typecheck test bench demo serve ui

sync:
	uv sync --python 3.13 --all-extras

lint:
	uv run ruff format --check src tests
	uv run ruff check src tests

typecheck:
	uv run mypy src tests

test:
	uv run pytest --cov=verispec --cov-report=term-missing --cov-fail-under=95

bench:
	uv run verispec bench

demo:
	uv run verispec demo

serve:
	uv run verispec serve --port 8200

ui:
	cd ui && npm ci && npm run build
