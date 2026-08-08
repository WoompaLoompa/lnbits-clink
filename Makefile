all: format check

format: prettier black ruff

check: mypy pyright checkblack checkruff checkprettier

prettier:
	uv run ./node_modules/.bin/prettier --write .
pyright:
	uv run ./node_modules/.bin/pyright

mypy:
	uv run mypy .

black:
	uv run black .

ruff:
	uv run ruff check . --fix

checkruff:
	uv run ruff check .

checkprettier:
	uv run ./node_modules/.bin/prettier --check .

checkblack:
	uv run black --check .

test:
	PYTHONUNBUFFERED=1 \
	DEBUG=true \
	PYTEST_PYTHON="$${PYTEST_PYTHON:-}" \
	bash tests/run_tests.sh

pre-commit:
	uv run pre-commit run --all-files

checkbundle:
	@echo "skipping checkbundle"
