.PHONY: format lint check test install-dev clean

# Install development dependencies
install-dev:
	pip3 install -r requirements-dev.txt

# Format code with Black and isort
format:
	python3 -m black .
	python3 -m isort .

# Run linting checks
lint:
	python3 -m flake8 . --count
	python3 -m mypy market_maker --ignore-missing-imports

# Run both formatting and linting
check: format lint

# Run tests (when available)
test:
	python3 -m pytest test/ -v

# Clean up temporary files
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +

# Install pre-commit hooks
install-hooks:
	pre-commit install

# Run pre-commit on all files
pre-commit-all:
	pre-commit run --all-files

# Help target
help:
	@echo "Available targets:"
	@echo "  install-dev     - Install development dependencies"
	@echo "  format          - Format code with Black and isort"
	@echo "  lint            - Run linting with flake8 and mypy"
	@echo "  check           - Run both formatting and linting"
	@echo "  test            - Run tests"
	@echo "  clean           - Clean up temporary files"
	@echo "  install-hooks   - Install pre-commit hooks"
	@echo "  pre-commit-all  - Run pre-commit on all files"
	@echo "  help            - Show this help message"
