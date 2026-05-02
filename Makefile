.PHONY: install run run-port stop free-port test lint-check dev check-api demo-failure-recovery demo-aws-control-plane demo-mock demo-scenario scenario-test topology-test ci

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

demo-scenario:
	./scripts/demo_scenario.sh

# Requires API already running. First workflow: CLOUDNET_PROVIDER=mock make dev, then make demo-mock.
# Exit 0 = scenario PASSED.
scenario-test:
	./scripts/cloudnet run examples/backend-failure.yaml

topology-test:
	$(PYTHON) -m pytest tests/test_topology_validation_errors.py tests/test_topology_golden_examples.py tests/test_topology_supported_scenario.py

ci: lint-check test topology-test
