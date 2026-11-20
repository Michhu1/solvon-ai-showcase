PY ?= python

.PHONY: install lint test eval demo run up down index-azure

install:            ## dev install with eval extras
	$(PY) -m pip install -e ".[dev,eval]"

lint:
	ruff check . && ruff format --check .

test:               ## offline, no keys needed
	$(PY) -m pytest

eval:               ## offline eval with quality gate, logged to MLflow
	$(PY) evals/run_eval.py

demo:               ## assess one sample claim in the terminal
	$(PY) -m claims_assistant.cli

run:                ## API on http://localhost:8000/docs
	uvicorn claims_assistant.api:app --reload

up:                 ## API + MLflow UI in Docker
	docker compose up --build -d

down:
	docker compose down

index-azure:        ## push the policy clauses to Azure AI Search (needs AZURE_SEARCH_* in .env)
	$(PY) -m claims_assistant.index_azure
