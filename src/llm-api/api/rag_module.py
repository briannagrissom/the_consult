import os
from typing import Any, Dict, List, Tuple

import chromadb

# Vertex AI
from google import genai
from google.genai import types

"""
This is a utility file that contain RAG related functions which will be called in the LLM-api pipeline
1. Takes a user query as input.
2. Generates an embedding for the query using Vertex AI.
3. Connects to a ChromaDB instance to retrieve relevant documents based on the query embedding,
   using metadata filtering, if provided.
4. Constructs a prompt using the retrieved documents and the user query.
"""

# ! Configuration variables
GCP_PROJECT = os.environ.get("GCP_PROJECT", "local-test-project")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

CHROMADB_HOST = os.environ.get("CHROMADB_HOST", "vector-db")
CHROMADB_PORT = int(os.environ.get("CHROMADB_PORT", "8000"))
CHROMADB_COLLECTION = "pubmed_abstract_semantic"
CHROMADB_TOP_K = int(os.environ.get("CHROMADB_TOP_K", "20"))
CHROMADB_CANDIDATE_K = int(os.environ.get("CHROMADB_CANDIDATE_K", "20"))
CHROMADB_FILTERED_TOP_K = int(os.environ.get("CHROMADB_FILTERED_TOP_K", "5"))

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-004")
EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "256"))


# llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
# def get_llm_client(): return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

llm_client = None


def get_llm_client():
    global llm_client
    if llm_client is None:
        llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    return llm_client


# Generate embedding for a query
def generate_query_embedding(query):
    llm_client = get_llm_client()
    kwargs = {"output_dimensionality": EMBEDDING_DIMENSION}
    response = llm_client.models.embed_content(
        model=EMBEDDING_MODEL, contents=query, config=types.EmbedContentConfig(**kwargs)
    )
    return response.embeddings[0].values


def get_chromadb_collection():
    # Connect to chroma DB
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)

    # Get the collection
    collection = client.get_collection(name=CHROMADB_COLLECTION)

    return collection


def _normalize_bool(value: Any) -> bool:
    """Treat common truthy strings/ints/bools uniformly."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "t", "yes", "y"}
    return bool(value)


def _build_metadata_filter(frontend_filters: Dict[str, Any] | None) -> Dict[str, Any]:
    """Translate UI filters into backend-friendly flags for local filtering."""
    if not frontend_filters:
        return {}

    filter_flags: Dict[str, Any] = {}

    impacts = set(frontend_filters.get("articleImpact") or [])
    if "Top Journal" in impacts:
        filter_flags["top_journal"] = True

    pub_date = frontend_filters.get("publicationDate")
    if pub_date == "Within last year":
        filter_flags["last_year"] = True
    elif pub_date == "Within last 5 years":
        filter_flags["last_5_years"] = True

    coi_choice = frontend_filters.get("coiDisclosure")
    if coi_choice == "With Disclosures":
        filter_flags["coi_required"] = True
    elif coi_choice == "Without Disclosures":
        filter_flags["coi_required"] = False

    return filter_flags


def _metadata_matches_filters(metadata: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """Return True when a metadata dict satisfies the selected filters."""
    if not filters:
        return True

    if filters.get("top_journal") and not _normalize_bool(metadata.get("is_top_journal")):
        return False

    if filters.get("last_year") and not _normalize_bool(metadata.get("is_last_year")):
        return False
    if filters.get("last_5_years") and not _normalize_bool(metadata.get("is_last_5_years")):
        return False

    if "coi_required" in filters:
        has_disclosure = _normalize_bool(metadata.get("coi_flag"))
        if filters["coi_required"] and not has_disclosure:
            return False
        if filters["coi_required"] is False and has_disclosure:
            return False

    return True


def query_documents(
    embedded_query, frontend_filters: Dict[str, Any] | None = None, n_results: int | None = None
) -> List[Dict[str, Any]]:
    """Query ChromaDB then apply filters locally to reduce load on the DB."""
    collection = get_chromadb_collection()
    filter_flags = _build_metadata_filter(frontend_filters)
    requested_k = n_results or CHROMADB_TOP_K
    final_k = min(requested_k, CHROMADB_FILTERED_TOP_K)
    candidate_k = max(final_k, CHROMADB_CANDIDATE_K)

    query_kwargs = {
        "query_embeddings": [embedded_query],
        "n_results": candidate_k,
        "include": ["documents", "metadatas", "distances"],
    }
    results = collection.query(**query_kwargs)

    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0] if "distances" in results else []

    merged: List[Dict[str, Any]] = []
    for idx, doc in enumerate(docs):
        merged.append(
            {
                "id": ids[idx] if idx < len(ids) else str(idx),
                "content": doc,
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "distance": distances[idx] if idx < len(distances) else None,
            }
        )

    if filter_flags:
        merged = [item for item in merged if _metadata_matches_filters(item.get("metadata", {}) or {}, filter_flags)]

    deduped: List[Dict[str, Any]] = []
    seen_pmids: set[str] = set()
    for item in merged:
        pmid = (item.get("metadata") or {}).get("pmid")
        key = str(pmid) if pmid is not None else item.get("id")
        if key in seen_pmids:
            continue
        seen_pmids.add(key)
        deduped.append(item)

    return deduped[:final_k]


def build_context_and_citations(
    question: str, frontend_filters: Dict[str, Any] | None = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """Retrieve supporting passages and build a context block plus citation payload."""
    embedded_query = generate_query_embedding(question)
    results = query_documents(embedded_query, frontend_filters=frontend_filters)

    context_lines: List[str] = []
    citations: List[Dict[str, Any]] = []

    for idx, item in enumerate(results, start=1):
        meta = item.get("metadata", {}) or {}
        context_lines.append(
            f"[{idx}] Title: {meta.get('title')}\n"
            f"Journal: {meta.get('journal_title')}\n"
            f"Date: {meta.get('publication_date')}\n"
            f"URL: {meta.get('pubmed_url')}\n"
            f"Snippet: {item.get('content')}"
        )
        citations.append(
            {
                "id": item.get("id"),
                "pmid": meta.get("pmid"),
                "title": meta.get("title"),
                "authors": meta.get("authors") or meta.get("author_list_full"),
                "journal": meta.get("journal_title"),
                "publication_date": meta.get("publication_date"),
                "pubmed_url": meta.get("pubmed_url"),
                "coi_flag": meta.get("coi_flag"),
                "is_last_year": meta.get("is_last_year"),
                "is_last_5_years": meta.get("is_last_5_years"),
                "is_top_journal": meta.get("is_top_journal"),
                "snippet": item.get("content"),
            }
        )

    context_block = "\n\n".join(context_lines)
    return context_block, citations
