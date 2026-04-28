.PHONY: install run run-port stop free-port test lint-check dev

PORT ?= 8010
PYTHON ?= python3

install:
	pip install -r backend/requirements.txt

run:
	./scripts/run_backend.sh 8010

run-port:
	./scripts/run_backend.sh $(PORT)

stop:
	-pkill -f "uvicorn app.main:app" 2>/dev/null || true

free-port:
	./scripts/free_port.sh 8010

test:
	./scripts/test_backend.sh

lint-check:
	$(PYTHON) -m compileall backend/app

dev: stop run
