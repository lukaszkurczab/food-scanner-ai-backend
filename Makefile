.PHONY: lint

lint:
	@if [ ! -x .venv/bin/ruff ]; then \
		echo "ruff is not installed in .venv. Run: .venv/bin/pip install -r requirements.txt"; \
		exit 1; \
	fi
	.venv/bin/ruff check .
