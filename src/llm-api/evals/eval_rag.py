"""
Offline RAG/LLM quality eval for The Consult.

Run it against real infrastructure (real ChromaDB + real OpenAI + real Braintrust,
so it costs actual API calls) from src/llm-api with:

    uv run --env-file ../../.env braintrust eval evals/eval_rag.py

Requires OPENAI_API_KEY and BRAINTRUST_API_KEY to be set.

Reference-free scorers (Faithfulness, ContextRelevancy, AnswerRelevancy) only need
`input` -- they're safe to run right away. Reference-based scorers (ContextPrecision,
ContextRecall, AnswerCorrectness) additionally compare against `expected`, a ground-truth
correct answer. Every row below has expected=None (skipped by those three scorers) --
fill in real, clinician-reviewed answers before trusting their scores. Don't invent
medical ground truth here.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `api.*` importable

# autoevals' scorers otherwise route their grading calls through Braintrust's AI
# gateway, which 404s unless an OpenAI key is separately configured in the Braintrust
# account's Settings -> AI Providers. Call OpenAI directly with our own key instead.
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")

# autoevals' own default scorer model (gpt-5-nano) silently returns empty extractions
# for these RAGAS scorers' structured tool-call prompts. gpt-5.4-nano -- the model this
# app already uses for real generation -- works correctly, so reuse it here too.
SCORER_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")

# Same project as api.server's live-request tracing, so eval experiments and production
# traces show up in one place in the Braintrust dashboard instead of two.
BRAINTRUST_PROJECT = os.environ.get("BRAINTRUST_PROJECT", "The Consult")

from autoevals import (  # noqa: E402
    AnswerCorrectness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    ContextRelevancy,
    Faithfulness,
    Score,
)
from braintrust import Eval  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from api.prompt import SYSTEM_PROMPT  # noqa: E402
from api.rag_module import build_context_and_citations  # noqa: E402
from api.server import AskRequest, _build_first_turn_message, get_llm_client  # noqa: E402

DATASET = [
    {"input": "What are common risk factors for type 2 diabetes?", 
    "expected": None},
    {"input": "What causes migraines?", "expected": None},
    {"input": "What are the main risk factors for hypertension?", "expected": None},
    {"input": "What does the evidence say about statins and cardiovascular risk?", "expected": None},
    {"input": "What treatments help with breathing problems in COPD patients?", "expected": None},
]


def rag_task(question: str) -> dict:
    """Runs the real retrieve-then-generate pipeline used by /api/ask's first turn."""
    payload = AskRequest(question=question)
    context_block, _citations = build_context_and_citations(question, None)
    message = _build_first_turn_message(payload, context_block=context_block)
    response = get_llm_client().invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(message)])
    return {"answer": response.content, "context": context_block}


def _make_scorer(scorer_cls, name: str, needs_expected: bool = False):
    """Adapts an autoevals RAG scorer (which wants input/output/context[/expected]) to
    Eval()'s (input, output, expected) scorer signature, pulling answer/context out of
    the dict rag_task() returns."""

    def scorer(input, output, expected=None):
        if needs_expected and not expected:
            return None  # nothing to compare against yet -- skip rather than error
        result = scorer_cls(model=SCORER_MODEL).eval(
            input=input, output=output["answer"], context=output["context"], expected=expected
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
context_precision = _make_scorer(ContextPrecision, "ContextPrecision", needs_expected=True)
context_recall = _make_scorer(ContextRecall, "ContextRecall", needs_expected=True)
answer_correctness = _make_scorer(AnswerCorrectness, "AnswerCorrectness", needs_expected=True)


Eval(
    BRAINTRUST_PROJECT,
    data=lambda: DATASET,
    task=rag_task,
    scores=[
        faithfulness,
        context_relevancy,
        answer_relevancy,
        context_precision,
        context_recall,
        answer_correctness,
    ],
)
