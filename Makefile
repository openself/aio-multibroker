.PHONY: lint lint-fix format test test-v check all

# Run ruff linter (check only, no changes)
lint:
	poetry run ruff check multibroker/ tests/

# Run ruff linter and auto-fix safe issues
lint-fix:
	poetry run ruff check --fix multibroker/ tests/

# Run ruff formatter
format:
	poetry run ruff format multibroker/ tests/

# Run tests
test:
	poetry run pytest tests/

# Run tests with verbose output
test-v:
	poetry run pytest tests/ -v -W error::DeprecationWarning

# Full check: lint + tests
check: lint test

# Fix everything, then verify
all: lint-fix format test
