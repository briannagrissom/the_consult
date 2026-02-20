# The Consult

AI assistant that delivers referenced, clinically aware answers for clinicians and researchers. It pairs Gemini with retrieval over PubMed-derived content so users can see citations, study details, and configurable evidence filters.

## What’s inside
- `.github/workflows`: GitHub Actions CI/CD pipelines, ML workflow.
- `notebooks`, `docs`, `data`: Exploratory work, notes, and seeds.
- `screenshots`: Images of the output of each GitHub Actions workflow, testing coverage reports.
- `src/llm-api`: FastAPI service that proxies Gemini, applies retrieval (ChromaDB), and streams responses.
- `src/frontend`: Vite/React client that calls the API and renders citations/filters.
- `src/models`: Data prep utilities for embeddings and ChromaDB ingestion.
- `src/datapipeline`: Data movement helpers.
- `src/deployment`: Pulumi programs and Dockerfiles for deploying the app to GCP (images + GKE).
- `src/workflow`: ML workflow CLI and support files used by `.github/workflows/ml-ci-cd-gcp.yml`.
- `tests/integration`: Integration tests.
- `tests/system`: System tests.

## Prerequisites
- Python 3.11+
- Node 18+ (for the frontend)
- Access to Vertex AI Gemini + embeddings (service account or ADC)
- ChromaDB endpoint with PubMed embeddings (defaults via env vars)

## Setup
```bash
git clone <repo> the_consult
cd the_consult
python -m venv .venv
source .venv/bin/activate
pip install uv
uv sync
```
Install frontend deps:
```bash
cd src/frontend
npm install
cd ../..
```

### Environment
set the following environment variables:
- `GCP_PROJECT`, `GCP_REGION` (default `us-central1`)
- `GEMINI_MODEL` (e.g., `gemini-2.5-flash`)
- `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`
- `CHROMADB_HOST`, `CHROMADB_PORT`, `CHROMADB_TOP_K`
- `API_ALLOW_ORIGINS` (comma-separated origins; defaults include `http://localhost:8080`)

## Deployment
### Kubernetes and GCP Deployment
Deployment scripts are implemented in `src/deployment`, using Pulumi.
Steps
```bash
cd src/deployment/deploy_images

# first time only & Set appropriate project name and region
pulumi stack init dev
pulumi config set gcp:project ac215-project
pulumi config set gcp:region us-central1

# Build and push the container to GCP Artefact registry
pulumi up --stack dev --refresh -y

cd ..
cd deploy_kubes

# first time only & Set appropriate project name, region, and service accounts
pulumi stack init dev
pulumi config set gcp:project ac215-project
pulumi config set gcp:region us-central1
pulumi config set security:gcp_service_account_email: deployment@apcomp215-project.iam.gserviceaccount.com
pulumi config set security:gcp_ksa_service_account_email: gcp-service@apcomp215-project.iam.gserviceaccount.com

# Deployment
pulumi up --stack dev --refresh -y
```

### CI/CD Pipelines and Machine Learning Workflow
The following GitHub Actions workflows are defined in `.github/workflows`:

- `ci-cd-main.yml`: primary CI pipeline. Builds the API image, runs:
  - linting and formatting (Black + Flake8)
  - unit tests (models + llm-api)
  - integration and system tests.
- `app-ci-cd-gcp.yml`: application deployment pipeline. Builds Docker images (frontend, llm-api, models), runs Pulumi (`deploy_images`, `deploy_kubes`), and deploys the app to GKE in GCP.
- `ml-ci-cd-gcp.yml`: ML workflow pipeline. Uses `src/workflow/cli.py` to submit Vertex AI `PipelineJob`s for:
  - data collection,
  - data processing,
  - model training,
  - model deployment.

Entrypoints and triggers:
- `ci-cd-main.yml`: runs automatically on pushes to main branch.
- `app-ci-cd-gcp.yml`: add `/deploy-app` to the commit message to run the app deployment pipeline.
- `ml-ci-cd-gcp.yml`: add `/run-` to the commit message to run the ML workflow.
    - Add `/run-data-collector` to run the data collector step.
    - Add `/run-data-processor` to run the data processor step.
    - Add `/run-ml-pipeline` to run the entire Vertex AI ML pipeline.

Note: The workflows `app-ci-cd-gcp.yml` and `ml-ci-cd-gcp.yml`, and the script `src/workflow/docker-shell.sh`, assume the following environment / secrets are configured in GitHub and/or your shell:

- `GCP_PROJECT`
- `GCS_SERVICE_ACCOUNT`
- `PULUMI_BUCKET`
- `GOOGLE_APPLICATION_CREDENTIALS` (path to a JSON key for the above service account)

One must configure them manually for the deployment and ML workflows to authenticate to GCP.

## Usage Details
### Run locally
API (from repo root):
```bash
uvicorn api.server:app --app-dir src/llm-api --host 0.0.0.0 --port 8081 --reload
```
Frontend:
```bash
cd src/frontend
npm run dev -- --host --port 8080
```
Open `http://localhost:8080` (UI calls the API on `http://localhost:8081`).

### Docker
Build and run the API:
```bash
cd src/llm-api
docker build -t the-consult-api .
docker run -p 8081:8081 --env-file ../.env.local the-consult-api
```
### Docker shells per module [Recommended Methods for Running Locally]
Each module ships a helper script to drop you into a containerized shell with its dependencies:
- API: `cd src/llm-api && ./docker-shell.sh`
- Frontend: `cd src/frontend && ./docker-shell.sh`
- Models (data prep): `cd src/models && ./docker-shell.sh`
- Datapipeline: `cd src/datapipeline && ./docker-shell.sh`

### Testing
Unit tests (individual to containers):
```bash
pytest src/llm-api/tests
pytest src/models/tests
```
Integration and system tests:
```bash
pytest tests/integration
pytest tests/system
```
Frontend tests (if configured):
```bash
cd src/frontend
npm test
```

## Issues and Limitations
- It was intended to develop additional study filters in our application for the type of study and the quality of the response's evidence. However, due to time limitations, these filters were omitted.
- There remains room for improvement in the llm-finetuning of the model's responses for clinical and research purposes. 
- It was intended for the application to be able to facilitate a series of questions from the user and subsequent answers, in a conversational format. Due to time limitations, we were not able to implement the ability of the model to receive and answer additional questions. 
- Additional user modes, that are more specific than research and clinical purposes, can be developed in the future. 

### Documentation of Testing Limitations
- Unit tests were only developed for the backend folders `src/llm-api` and `src/models`. Unit testing could have been expanded to `src/datapipeline` to ensure the proper downloading and extracting of literature data form PubMed.
- The coverage reports (in `screenshots`) reveal that both backend folders have a fair amount of testing coverage. 
    - In `src/llm-api`, the functions get_chromadb_collection(), query_documents(), get_index(), health_check(), and build_context_and_citations() had minimal coverage.
    - In `src/models`, the functions in `query_rag_model.py` and `src/gcs.py` had minimal coverage.
- Limited artifact storage in GitHub prohibited the generation of more up-to-date coverage reports as html files. 