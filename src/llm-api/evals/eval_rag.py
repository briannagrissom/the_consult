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
    {
        "input": "What are common risk factors for type 2 diabetes?",
        "expected": (
            "The primary risk factors for type 2 diabetes mellitus (T2DM) include a combination of genetic "
            "predisposition, components of metabolic syndrome, and lifestyle variables. According to American "
            "Diabetes Association (ADA) guidelines, targeted screening is indicated for asymptomatic adults with "
            "overweight or obesity (BMI ≥ 25 kg/m², or ≥ 23 kg/m² in Asian American populations) who "
            "exhibit additional cardiometabolic risk markers. These clinical markers encompass hypertension (blood "
            "pressure ≥ 130/80 mmHg or on active therapy), dyslipidemia (HDL cholesterol < 35 mg/dL and/or "
            "triglycerides > 250 mg/dL), a history of cardiovascular disease, or clinical conditions associated "
            "with severe insulin resistance, such as polycystic ovary syndrome (PCOS) and acanthosis nigricans. "
            "Demographic and historical variables also heavily influence risk stratification, including having a "
            "first-degree relative with diabetes or belonging to a high-risk ethnic demographic (e.g., African "
            "American, Hispanic/Latino, Native American, or Pacific Islander). Furthermore, a prior diagnosis of "
            "gestational diabetes mellitus (GDM) or current evidence of prediabetes (A1C 5.7–6.4%, fasting "
            "plasma glucose 100–125 mg/dL, or a 2-hour 75-g OGTT of 140–199 mg/dL) necessitates heightened "
            "clinical surveillance due to a significantly accelerated rate of progression to overt T2DM. Key "
            "Takeaway: T2DM risk assessment relies on identifying patients with elevated BMI alongside established "
            "metabolic, genetic, and demographic comorbidities to guide appropriate diagnostic screening."
        ),
    },
    {
        "input": "How do you treat eczema?",
        "expected": (
            "The management of atopic dermatitis (eczema) employs a stepwise clinical approach that prioritizes "
            "epidermal barrier restoration alongside targeted topical or systemic pharmacotherapy based on disease "
            "severity. Standard-of-care foundational therapy requires the daily application of thick, "
            "ceramide-containing emollients and the strict avoidance of exacerbating triggers. For acute, "
            "mild-to-moderate flares, topical corticosteroids (TCS) remain the first-line intervention; clinicians "
            "typically utilize low-potency agents (e.g., hydrocortisone 2.5%) for the face and intertriginous "
            "folds, reserving medium-to-high potency formulations (e.g., triamcinolone 0.1% or betamethasone) for "
            "lichenified plaques on the trunk and extremities. Topical calcineurin inhibitors (e.g., tacrolimus "
            "0.1% ointment) or PDE4 inhibitors (e.g., crisaborole) are frequently employed as steroid-sparing "
            "alternatives to mitigate cutaneous atrophy during proactive maintenance or when treating sensitive "
            "regions. In moderate-to-severe or refractory cases, systemic immunomodulation is indicated to achieve "
            "adequate disease control. Biologic agents targeting the Type 2 inflammatory cascade, notably "
            "dupilumab (an IL-4Rα antagonist), have established a robust efficacy and long-term safety "
            "profile, often yielding a >75% improvement in the Eczema Area and Severity Index (EASI-75). "
            "Alternatively, oral Janus kinase (JAK) inhibitors (e.g., upadacitinib, abrocitinib) provide rapid and "
            "profound symptomatic relief, though patient selection demands careful risk stratification regarding "
            "venous thromboembolism and major adverse cardiovascular events (MACE). Key Takeaway: Eczema treatment "
            "requires continuous barrier repair, complemented by topical corticosteroids or calcineurin inhibitors "
            "for localized flares, and advanced systemic agents like biologics or JAK inhibitors for severe, "
            "refractory disease."
        ),
    },
    {
        "input": "What causes migraines?",
        "expected": (
            "Migraine is a complex neurobiological disorder primarily driven by cortical spreading depression and "
            "the subsequent activation of the trigeminovascular system. The prevailing pathophysiologic model "
            "centers on the release of vasoactive neuropeptides, most notably calcitonin gene-related peptide "
            "(CGRP), from trigeminal sensory nerve terminals. This release precipitates sterile neurogenic "
            "inflammation, marked vasodilation of meningeal blood vessels, and progressive sensitization of "
            "central and peripheral nociceptive pathways. Cortical spreading depression—a slow-propagating "
            "wave of neuronal and glial depolarization—is widely recognized as the physiological correlate of "
            "migraine aura and a key trigger for initiating this cascading trigeminal activation. Underlying this "
            "neurovascular vulnerability is a robust genetic predisposition, which is largely polygenic, though "
            "specific monogenic channelopathies (e.g., CACNA1A or ATP1A2 mutations) are observed in familial "
            "hemiplegic variants. In these susceptible patients, acute attacks are frequently precipitated by "
            "internal or external triggers, such as estrogen withdrawal, sleep dysregulation, or distinct "
            "environmental stressors, which collectively lower the threshold for trigeminovascular firing. Key "
            "Takeaway: Migraine pathogenesis is rooted in genetic neurovascular excitability and CGRP-mediated "
            "trigeminal inflammation, mechanisms that now serve as the primary targets for modern migraine-"
            "specific biologic and small-molecule pharmacotherapies."
        ),
    },
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
