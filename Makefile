.PHONY: install run test

install:
	pip install -r backend/requirements.txt

run:
	cd backend && uvicorn app.main:app --reload --port 8010

test:
	cd backend && pytest
