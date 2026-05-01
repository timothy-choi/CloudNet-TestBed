.PHONY: install run run-port stop free-port test lint-check dev check-api demo-failure-recovery demo-aws-control-plane demo-mock ci

PORT ?= 8010
PYTHON ?= python3

install:
	$(PYTHON) -m pip install -r backend/requirements.txt

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

check-api:
	./scripts/check_api.sh

demo-failure-recovery:
	./scripts/demo_failure_recovery.sh

demo-aws-control-plane:
	./scripts/demo_aws_control_plane.sh

demo-mock:
	./scripts/demo_mock_control_plane.sh

ci: lint-check test
