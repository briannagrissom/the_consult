# Models

## Overview

This module now focuses solely on the Retrieval-Augmented Generation (RAG) tooling:
chunking corpora, generating embeddings, loading them into ChromaDB, and running ad-hoc
queries. The FastAPI Gemini proxy was moved to `src/llm-api`, so treat that directory
as the deployment unit for the LLM service.

## Serving Model through Fast API

run this code
```
uv run uvicorn api.server:app --reload --host 0.0.0.0 --port 8081
```


## Developing thorugh Terraform instance VM (GCP)

I added a new workflow on developing a new VM on GCP through Terraform. (According to the ChromaDB docs)

### Step 1: Initiate the VM
1. Setup your variables in `trf/chroma.tfvars`
2. cd into trf dir
3. run
```bash
terraform init
terraform plan -var-file chroma.tfvars
terraform apply -var-file chroma.tfvars
```
### Step 2: Update the database with embedding or parquet file

- If update your db through parquest files (from raw to new embedding) run `parquet_to_chromadb.py`.
- If update through previously embedded .jsonl run `jsonl_to_chromadb.py`
