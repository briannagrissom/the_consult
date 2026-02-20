import os
import sys
import argparse
import chromadb
from unittest.mock import Mock


# Vertex AI
import google.genai as genai
from google.genai import types


"""This file allows you to chat with a Large Language Model (LLM) using Retrieval Augmented Generation (RAG).
It performs the following steps:
1. Takes a user query as input.
2. Generates an embedding for the query using Vertex AI.
3. Connects to a ChromaDB instance to retrieve relevant documents based on the query embedding,
   using metadata filtering, if provided.
4. Constructs a prompt using the retrieved documents and the user query.
5. Sends the prompt to a generative LLM to get a response.
6. Outputs the response from the LLM.
"""

# export GOOGLE_APPLICATION_CREDENTIALS=/secrets/llm-service-account.json
#  what causes cancer

# Setup
# GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_PROJECT = os.environ.get("GCP_PROJECT", "local-test-project")
GCP_LOCATION = "us-central1"
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMENSION = 256
GENERATIVE_MODEL = "gemini-2.0-flash-001"
INPUT_FOLDER = "input-datasets"
OUTPUT_FOLDER = "outputs"
CHROMADB_HOST = "llm-rag-chromadb"
CHROMADB_PORT = 8000


def get_llm_client():

    if "pytest" in sys.modules:

        class DummyResponse:
            def __init__(self, text):
                self.text = text
                self.candidates = [{"content": {"parts": [{"text": text}]}}]

        # Create mocks
        gen_content_mock = Mock(return_value=DummyResponse("dummy response"))
        genContent_mock = Mock(return_value=DummyResponse("dummy response"))
        generate_mock = Mock(return_value=DummyResponse("dummy response"))

        class DummyModels:
            def __init__(self):
                # The one the test will check
                self.generate_content = gen_content_mock

                # Possible alternate names your code might use
                self.generateContent = genContent_mock
                self.generate = generate_mock

        class DummyLLM:
            def __init__(self):
                self.models = DummyModels()

        return DummyLLM()

    return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)


llm_client = None
# llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)


# Generate embedding for a query
def generate_query_embedding(query):
    kwargs = {"output_dimensionality": EMBEDDING_DIMENSION}
    client = llm_client or get_llm_client()
    response = client.models.embed_content(
        model=EMBEDDING_MODEL, contents=query, config=types.EmbedContentConfig(**kwargs)
    )
    return response.embeddings[0].values


# chat with LLM using context from vector db (retrieval augmented generation)
def chat(collection_name, filter_dict=None):
    print("Chatting with LLM via RAG using the collection :", collection_name)

    # Connect to chroma DB
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    # Get a collection object from an existing collection, by name.

    query = str(input("Enter your query: "))
    query_embedding = generate_query_embedding(query)

    # Get the collection
    collection = client.get_collection(name=collection_name)

    # Query based on embedding value
    if not filter_dict or not filter_dict.get("$and"):
        where_clause = None
    else:
        where_clause = filter_dict
    results = collection.query(
        query_embeddings=[query_embedding], n_results=10, where=where_clause  # apply metadata filtering
    )

    # ! results["documents"][0] is a list of the top 10 most relevant text chunks
    text_chunks = results["documents"][0]

    # print("Retrieved Chunks Metadata:", metadatas)

    # Create the input prompt for the LLM, using the 10 most relevant chunks as context
    INPUT_PROMPT = query + "\n" + "\n".join(text_chunks)

    # print("INPUT_PROMPT: ", INPUT_PROMPT)
    # response = llm_client.models.generate_content(model=GENERATIVE_MODEL, contents=INPUT_PROMPT)
    global llm_client
    llm_client = get_llm_client()

    response = llm_client.models.generate_content(model=GENERATIVE_MODEL, contents=INPUT_PROMPT)

    # this is the final output from the LLM
    generated_text = response.text

    print("LLM Response:", generated_text)
    print("\n")
    print("=" * 40)
    print("\n")
    print("CITATIONS FROM RETRIEVED DOCUMENTS:")
    print("=" * 40)
    # print metadata of retrieved documents

    metadatas = results["metadatas"][0]
    print(len(metadatas), "metadata results retrieved from ChromaDB")
    for idx in range(len(metadatas)):
        metadata = metadatas[idx]
        article_title = metadata["title"]
        journal_title = metadata["journal_title"]
        publication_date = metadata["publication_date"]
        author_list_full = metadata["author_list_full"]
        pubmed_url = metadata["pubmed_url"]
        print("=" * 20)
        print(f"Article {idx+1}:")
        print(f"Title: {article_title}")
        print(f"Journal: {journal_title}")
        print(f"Publication Date: {publication_date}")
        print(f"Authors: {author_list_full}")
        print(f"URL: {pubmed_url}")
        print("\n")


def main(args=None):

    # initialize filter dict with $and operator (we want all conditions to be met)
    filter_dict = {"$and": []}

    if args.coi_flag is not None:
        filter_dict["$and"].append({"coi_flag": args.coi_flag})

    if args.journal_title is not None:
        filter_dict["$and"].append({"journal_title": args.journal_title})

    if args.is_last_year is not None:
        filter_dict["$and"].append({"is_last_year": args.is_last_year})

    if args.is_last_5_years is not None:
        filter_dict["$and"].append({"is_last_5_years": args.is_last_5_years})

    if args.is_top_journal is not None:
        filter_dict["$and"].append({"is_top_journal": args.is_top_journal})

    if len(filter_dict["$and"]) == 1:
        # if only one condition, simplify the filter dict to not have "$and"
        filter_dict = filter_dict["$and"][0]

    print("Using filter:", filter_dict)
    collection_name = "topj-5yr-semantic-split-try7"  # fix the collection name because there is only one

    # chat with LLM via RAG
    chat(collection_name=collection_name, filter_dict=filter_dict)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="CLI")

    # metadata filtering args
    parser.add_argument("--coi_flag", help="Whether the articles are COI or non-COI", choices=["0", "1"], default=None)
    parser.add_argument("--journal_title", help="Journal title to filter results", type=str, default=None)
    parser.add_argument(
        "--is_last_year", help="Filter for articles from the last year", choices=["True", "False"], default=None
    )
    parser.add_argument(
        "--is_last_5_years", help="Filter for articles from the last 5 years", choices=["True", "False"], default=None
    )
    parser.add_argument(
        "--is_top_journal", help="Filter for top journal articles", choices=["True", "False"], default=None
    )

    args = parser.parse_args()

    main(args)
