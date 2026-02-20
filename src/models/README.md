# Models

## Overview

This module now focuses solely on the Retrieval-Augmented Generation (RAG) tooling:
chunking corpora, generating embeddings, loading them into ChromaDB, and running ad-hoc
queries. The FastAPI Gemini proxy was moved to `src/llm-api`, so treat that directory
as the deployment unit for the LLM service.

## How to run it

1. Replace `GCP_PROJECT` with your project name in the `docker-shell.sh` file

2. Create the docker image and run the docker container

```bash
sh docker-shell.sh
```
3. Chunk and embed the `.txt` files stored in the configured GCS bucket. `--chunk_type`
   can be `char-split`, `recursive-split`, or `semantic-split`.

```bash
uv run python chunk-embed.py --chunk_type char-split
```

4. Load the generated `embeddings-*.jsonl` files into a ChromaDB collection.

```bash
uv run python load.py --load --chunk_type char-split
```

5. Query the RAG model - ask the model health-related questions. Running this file will ask for user input.

```bash
uv run query-rag-model.py --chat --chunk_type char-split
```

## Example

A sample input is located in the file `sample-input`. The full dataset will be in `.txt` file format.

### Chunking the input data
<img src="images/chunking-sample-data.png"  width="400">


### Embedding the chunks
<img src="images/embedding-sample-data.png"  width="400">


### Loading embeddings into vector database
<img src="images/loading-embeddings-sample-data.png"  width="400">


### Querying the RAG Model
<img src="images/chat-sample-data3.png"  width="400">

Here, the input prompt is "How do I alleviate cancer?". Using the vector embedding dataset, the RAG and the LLM are able to answer this prompt.


## Next Steps

1. Obtain a much larger dataset, process it to `.txt` file format, and store the files in a GCP Bucket.
2. Retrain the model with the larger dataset via a GCP virtual machine.
3. Fine-tune the model.
4. Integrate the data collection, preprocessing, and model training steps into a single workflow that is able to be executed with one command.

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
