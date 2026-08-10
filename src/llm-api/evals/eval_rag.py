"""
Offline RAG/LLM quality eval for The Consult.

Run it against real infrastructure (real ChromaDB + real OpenAI + real Braintrust,
so it costs actual API calls) from src/llm-api with:

    uv run --env-file ../../.env braintrust eval evals/eval_rag.py

Requires OPENAI_API_KEY and BRAINTRUST_API_KEY to be set.

Only reference-free scorers run here: Faithfulness, ContextRelevancy and AnswerRelevancy
grade the answer against the retrieved context, needing no ground truth.

The reference-based scorers (ContextPrecision, ContextRecall, AnswerCorrectness) were
removed deliberately. They compare against `expected`, and a trustworthy `expected` for
these questions would have to be written from the indexed corpus by a clinician. Scoring
against guideline-level prose the corpus never contained measures the gap between the
reference and the corpus, not retrieval quality -- ContextRecall read a flat 0.00 across
every question for exactly that reason. Two further caveats made them worth less than
they cost: autoevals collapses `context` into a single string, so its ContextPrecision
emits one binary verdict rather than precision@k (it scored 1.00 everywhere), and each
scorer is an extra LLM grading call per row. Restore them only alongside real,
clinician-reviewed answers grounded in the corpus.

DATASET rows therefore carry only `input`. The previous `expected` answers were removed
rather than left unused: they were model-written, not clinician-reviewed, and leaving
authoritative-looking prose in the file invites someone to score against it.

The model now picks between two tools (search_pubmed, get_full_abstract) on every turn, so
rag_task() runs the same tool-calling loop as api.server.ask_llm() (rather than pre-fetching
context and doing a single non-tool-calling invoke) and records the full tool-call trajectory.
The deterministic scorers below (used_search_pubmed, tool_call_efficiency,
valid_full_abstract_pmid, citation_grounding, evidence_cited) grade *that* trajectory against
the system prompt's tool-use rules -- did it search at all, search redundantly, reference a
PMID it never actually saw, cite a number that doesn't exist, or retrieve evidence and then
ignore it. They're plain Python, not LLM judges, so they're cheap and deterministic.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `api.*` importable

# autoevals' scorers otherwise route their grading calls through Braintrust's AI
# gateway, which 404s unless an OpenAI key is separately configured in the Braintrust
# account's Settings -> AI Providers. Call OpenAI directly with our own key instead.
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")

# autoevals' own default scorer model (gpt-5-nano) silently returns empty extractions
# for these RAGAS scorers' structured tool-call prompts. gpt-5.4-nano (this app's live
# generation model) is *more* reliable but still flaky here -- measured ~25% empty-array
# failures on realistic multi-paper contexts, because Braintrust's tracing wraps the
# scorer's OpenAI client through the Responses API, and the nano tier struggles with
# this task's harder "extract sentences + give step-by-step reasons for each" shape.
# gpt-5.4-mini measured 0/8 failures in the same test with more sensible score spread,
# so scorer grading gets its own (larger, decoupled from the app's chat model) default.
SCORER_MODEL = os.environ.get("EVAL_SCORER_MODEL", "gpt-5.4-mini")

# Same project as api.server's live-request tracing, so eval experiments and production
# traces show up in one place in the Braintrust dashboard instead of two.
BRAINTRUST_PROJECT = os.environ.get("BRAINTRUST_PROJECT", "The Consult")

from autoevals import (  # noqa: E402
    AnswerRelevancy,
    ContextRelevancy,
    Faithfulness,
    Score,
)
from braintrust import Eval  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from api.prompt import SYSTEM_PROMPT  # noqa: E402
from api.server import (  # noqa: E402
    TOOL_MAX_ITERATIONS,
    AskRequest,
    Citation,
    _build_first_turn_message,
    _format_citations_block,
    _run_tool_calls,
    get_full_abstract,
    get_llm_client,
    search_pubmed,
)

DATASET = [
    {"input": "What are common risk factors for type 2 diabetes?"},
    {"input": "How do you treat eczema?"},
    {"input": "What causes migraines?"},
]


def rag_task(question: str) -> dict:
    """Runs the same tool-calling loop as /api/ask's ask_llm(): the model decides for itself
    whether, and how many times, to call search_pubmed and/or get_full_abstract before giving a
    final answer. Returns the answer plus the full tool-call trajectory and citations retrieved,
    so scorers can grade tool usage in addition to the final text."""
    payload = AskRequest(question=question)
    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(_build_first_turn_message(payload))]
    turn_citations: list[Citation] = []
    tool_calls: list[dict] = []
    full_abstracts: list[str] = []
    llm = get_llm_client().bind_tools([search_pubmed, get_full_abstract])

    response = None
    for _ in range(TOOL_MAX_ITERATIONS):
        response = llm.invoke(messages)
        messages = messages + [response]
        if not response.tool_calls:
            break
        tool_calls.extend({"name": call["name"], "args": call["args"]} for call in response.tool_calls)
        tool_messages = _run_tool_calls(response.tool_calls, None, turn_citations)
        # Keep successful get_full_abstract text: it is real evidence the model saw, but unlike
        # search results it never lands in turn_citations, so it has to be collected here.
        for call, message in zip(response.tool_calls, tool_messages):
            if call["name"] == "get_full_abstract" and not message.content.startswith("No stored abstract"):
                full_abstracts.append(message.content)
        messages = messages + tool_messages
    else:  # loop exhausted TOOL_MAX_ITERATIONS without a final answer -- same fallback as ask_llm
        response = get_llm_client().invoke(messages)

    # Score against the evidence itself, not the raw tool transcript. _execute_search also returns
    # control strings ("No relevant PubMed results were found...", "This turn's citation budget is
    # already used up...") that are instructions to the model, not retrieved context -- feeding
    # them to ContextRelevancy/Faithfulness dilutes the context with text no chunk ever contained.
    # turn_citations is the authoritative list of what was actually retrieved this turn.
    evidence: list[str] = []
    if turn_citations:
        evidence.append(_format_citations_block([c.model_dump() for c in turn_citations]))
    evidence.extend(full_abstracts)

    return {
        "answer": response.content,
        "context": "\n\n".join(evidence) or "No evidence retrieved.",
        "tool_calls": tool_calls,
        "citations": [c.model_dump() for c in turn_citations],
    }


def _make_scorer(scorer_cls, name: str):
    """Adapts an autoevals RAG scorer (which wants input/output/context) to Eval()'s
    (input, output, expected) scorer signature, pulling answer/context out of the dict
    rag_task() returns. Only reference-free scorers are wired up -- see the module
    docstring for why the reference-based ones were dropped."""

    def scorer(input, output, expected=None):
        result = scorer_cls(model=SCORER_MODEL).eval(
            input=input, output=output["answer"], context=output["context"]
        )
        return Score(name=name, score=result.score, metadata=result.metadata)

    scorer.__name__ = name
    return scorer


def faithfulness(input, output, expected=None):
    # autoevals 0.3.0's Faithfulness has a bug: it extracts statements from `expected`
    # instead of `output` (see autoevals/ragas.py, Faithfulness._run_eval_sync). With no
    # expected answer that's a `None`, giving zero extracted statements and a
    # ZeroDivisionError. Faithfulness is reference-free by design, so routing the real
    # answer through the `expected` param it (incorrectly) reads works around the bug
    # without needing a real ground-truth answer.
    result = Faithfulness(model=SCORER_MODEL).eval(
        input=input, output=output["answer"], context=output["context"], expected=output["answer"]
    )
    return Score(name="Faithfulness", score=result.score, metadata=result.metadata)


context_relevancy = _make_scorer(ContextRelevancy, "ContextRelevancy")
answer_relevancy = _make_scorer(AnswerRelevancy, "AnswerRelevancy")


# --- Tool-call scorers ---------------------------------------------------------------------
# Deterministic (no LLM judge), grading the tool-call trajectory rag_task() records rather than
# just the final answer text. Each returns None (skipped, not scored 0) when its precondition
# doesn't apply to a given row, e.g. no get_full_abstract call was made this turn.

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def used_search_pubmed(input, output, expected=None):
    # The system prompt requires calling search_pubmed on *every* medical/clinical question,
    # even ones the model is confident it already knows -- this app exists to ground answers in
    # retrieved evidence rather than parametric knowledge. Flags turns that skipped it entirely.
    called = any(call["name"] == "search_pubmed" for call in output["tool_calls"])
    return Score(name="UsedSearchPubmed", score=1.0 if called else 0.0, metadata={"tool_calls": output["tool_calls"]})


def tool_call_efficiency(input, output, expected=None):
    # Repeating an identical (case/whitespace-insensitive) search_pubmed query in the same turn
    # burns the tool-call budget without surfacing new evidence. Skipped (not 0) when
    # search_pubmed was never called -- used_search_pubmed already covers that failure mode.
    queries = [call["args"].get("query", "") for call in output["tool_calls"] if call["name"] == "search_pubmed"]
    if not queries:
        return None
    normalized = [q.strip().lower() for q in queries]
    score = len(set(normalized)) / len(normalized)
    return Score(name="ToolCallEfficiency", score=score, metadata={"queries": queries})


def valid_full_abstract_pmid(input, output, expected=None):
    # Per the system prompt, get_full_abstract should only be called on a PMID "you've already
    # seen" in this turn's search_pubmed results -- not one invented or misremembered. Skipped
    # when get_full_abstract wasn't called this turn (the current single-turn DATASET rarely
    # triggers this; it matters once follow-up rows are added).
    calls = [call for call in output["tool_calls"] if call["name"] == "get_full_abstract"]
    if not calls:
        return None
    known_pmids = {c.get("pmid") for c in output["citations"] if c.get("pmid")}
    valid = sum(1 for call in calls if call["args"].get("pmid") in known_pmids)
    return Score(
        name="ValidFullAbstractPmid",
        score=valid / len(calls),
        metadata={"calls": calls, "known_pmids": list(known_pmids)},
    )


def citation_grounding(input, output, expected=None):
    # Every bracketed number in the answer (e.g. "[3]") must refer to one of the results
    # actually returned this turn -- the system prompt's "never invent citations" rule, applied
    # to the citation *numbers* specifically. Skipped when the answer cites nothing.
    cited = [int(n) for n in CITATION_PATTERN.findall(output["answer"])]
    if not cited:
        return None
    num_citations = len(output["citations"])
    valid = sum(1 for n in cited if 1 <= n <= num_citations)
    return Score(
        name="CitationGrounding",
        score=valid / len(cited),
        metadata={"cited": cited, "num_citations": num_citations},
    )


def evidence_cited(input, output, expected=None):
    # search_pubmed returning results is only useful if the answer actually cites them. Scores 0
    # for a turn that retrieved evidence but cited none of it -- a "searched but ignored it"
    # failure distinct from skipping the search altogether. Skipped when nothing was retrieved.
    if not output["citations"]:
        return None
    cited = bool(CITATION_PATTERN.search(output["answer"]))
    return Score(name="EvidenceCited", score=1.0 if cited else 0.0)


Eval(
    BRAINTRUST_PROJECT,
    data=lambda: DATASET,
    task=rag_task,
    scores=[
        faithfulness,
        context_relevancy,
        answer_relevancy,
        used_search_pubmed,
        tool_call_efficiency,
        valid_full_abstract_pmid,
        citation_grounding,
        evidence_cited,
    ],
)
