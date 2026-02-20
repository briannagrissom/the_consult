import os
import argparse
import pandas as pd
import json

# import time
import glob
from sklearn.model_selection import train_test_split
from google.cloud import storage

# Gen AI
from google import genai
from google.genai import types

# from google.genai.types import Content, Part, GenerationConfig, ToolConfig
# from google.genai import errors

# Setup
GCP_PROJECT = "ac215-ms4"
GCP_LOCATION = "us-central1"
GENERATIVE_MODEL = "gemini-2.0-flash-001"
OUTPUT_FOLDER = "data"
GCS_BUCKET_NAME = "ac215-ms4-bucket"

#############################################################################
#                       Initialize the LLM Client                           #
llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
#############################################################################

safety_settings = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
]

# System Prompt
SYSTEM_INSTRUCTION = """
Generate 20 question–answer pairs in English about modern medical practice and biomedical research.

Answer style depends on the persona.

Question Independence
Each Q&A must be fully self-contained.
No references to previous questions.
The persona determines the required length and level of detail.

Persona Rules
Alternate personas: Clinician, then Researcher.
Clinician: answer must be extremely concise (no line breaks).
Researcher: answer must be considerably longer, analytical, richly detailed.

Content
Cover major clinical fields (cardiology, endocrinology, infectious disease, oncology, neurology, etc.).
Include both clinical reasoning and scientific/mechanistic questions.
Numerical or procedural details only when accurate.

Evidence
Cite real guidelines or studies only when actually known (CDC, WHO, NIH, NICE, peer-reviewed journals).
Use bracketed citations like [PMID:12345678].
Never create or guess citations.

Tone
Clinician: crisp, minimal, guideline-based.
Researcher: analytical, evidence-oriented.
No humor, no speculation.

Answer Structure
Clinician answers:
- Extremely concise
- No bullet points, no Key Takeaway.
- No line breaks within the answer.

Researcher answers:
- Considerably longer, analytical, richly detailed.

Complexity
Range from basic clinical concepts to advanced molecular or translational topics.

Question Types
Use “what”, “how”, “why”, and “compare/contrast” questions.

Accuracy & Safety
All information must be medically accurate and up-to-date.
No emergency instructions or personalized medical advice.

Output Format
Return a valid JSON array of 20 objects.
Each object must include "persona", "question", and "answer".
Use double quotes for all strings.
Escape internal single quotes with a backslash (\\') if needed.
Example JSON Output:
[
  {
    "persona": "Clinician",
    "question": "What is the recommended initial management for newly
    diagnosed stage 1 hypertension in an otherwise healthy adult?",
    "answer": "Initiate lifestyle modification and consider starting a
    thiazide-type diuretic or ACE inhibitor if blood pressure remains ≥130/80 mmHg
    after lifestyle measures per ACC/AHA guidelines."
  },
  {
    "persona": "Researcher",
    "question": "How do tumor-associated macrophage phenotypes influence immune
    evasion and therapeutic resistance in solid tumors?",
    "answer": "Tumor-associated macrophages (TAMs) exert profound effects on the tumor
    microenvironment by shifting between pro-inflammatory (M1-like) and immunosuppressive
    (M2-like) states, although in vivo they exist along a continuum rather than discrete
    categories. In many solid tumors, sustained exposure to IL-10, TGF-β, hypoxia, and
    tumor-derived metabolites drives TAM polarization toward an M2-like phenotype, which
    suppresses cytotoxic T-cell activity through multiple coordinated mechanisms. These
    include expression of PD-L1, secretion of arginase-1 that depletes local L-arginine,
    and production of prostaglandin E2 and IL-10, all of which limit T-cell proliferation
    and effector function. TAMs also promote regulatory T-cell expansion and inhibit
    antigen-presenting cell maturation, thereby weakening antitumor immunity at several
    checkpoints. Beyond immune evasion, TAMs facilitate therapeutic resistance through
    enhancement of angiogenesis via VEGF and matrix-remodeling enzymes, reducing drug
    penetration into tumor cores. They also release survival signals such as EGF, CCL18,
    and CSF1 that help tumor cells withstand cytotoxic stress from chemotherapy and
    radiotherapy. In targeted therapy contexts, TAM-derived cytokines activate bypass
    pathways—most notably JAK/STAT3 and PI3K/AKT—that diminish dependence on the inhibited
    oncogenic driver. Inhibition of CSF1R, depletion strategies, and reprogramming approaches
    (e.g., CD40 agonists or STING activators) are currently under investigation to shift TAMs
    toward immunostimulatory phenotypes and improve responses to checkpoint inhibitors and
    other systemic therapies. Overall, TAM plasticity forms a major axis of immune
    suppression and adaptive resistance, making their modulation a central focus in emerging
    combination treatment strategies."
  }
]
"""

response_schema = {
    "type": "array",
    "description": "Array of question and answer pairs",
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question being asked"},
            "answer": {
                "type": "string",
                "description": "The detailed answer to the question",
            },
            "persona": {"type": "string", "description": "Either Clinician or Researcher"},
        },
        "required": ["question", "answer", "persona"],
    },
}


def generate():
    print("generate()")

    # Make dataset folders
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    INPUT_PROMPT = """
    Generate 20 question–answer pairs about modern medical practice
    and biomedical research following the SYSTEM_INSTRUCTION. Alternate
    persona roles between Clinician and Researcher and output valid JSON."""
    NUM_ITERATIONS = 500  # INCREASE TO CREATE A LARGE DATASET

    # Configuration settings for the content generation
    GENERATION_CONFIG = types.GenerateContentConfig(
        temperature=0.9,
        top_p=0.95,
        max_output_tokens=8192,
        safety_settings=safety_settings,
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    # Loop to generate and save the content
    for i in range(0, NUM_ITERATIONS):
        print(f"Generating batch: {i}")
        try:

            response = llm_client.models.generate_content(
                model=GENERATIVE_MODEL,
                contents=INPUT_PROMPT,
                config=GENERATION_CONFIG,
            )
            generated_text = response.text

            # Create a unique filename for each iteration
            file_name = f"{OUTPUT_FOLDER}/medical_qa_{i}.txt"
            # Save
            with open(file_name, "w") as file:
                file.write(generated_text)
        except Exception as e:
            print(f"Error occurred while generating content: {e}")


def prepare():
    print("prepare()")

    # Get the generated files
    output_files = glob.glob(os.path.join(OUTPUT_FOLDER, "medical_qa_*.txt"))
    output_files.sort()

    # Consolidate the data
    output_pairs = []
    errors = []
    for output_file in output_files:
        print("Processing file:", output_file)
        with open(output_file, "r") as read_file:
            text_response = read_file.read()

        text_response = text_response.replace("```json", "").replace("```", "")

        try:
            json_responses = json.loads(text_response)
            output_pairs.extend(json_responses)

        except Exception as e:
            errors.append({"file": output_file, "error": str(e)})

    print("Number of errors:", len(errors))
    print(errors[:5])

    # Save the dataset
    output_pairs_df = pd.DataFrame(output_pairs)
    output_pairs_df.drop_duplicates(subset=["question"], inplace=True)
    output_pairs_df = output_pairs_df.dropna()
    print("Shape:", output_pairs_df.shape)
    print(output_pairs_df.head())
    filename = os.path.join(OUTPUT_FOLDER, "instruct-dataset.csv")
    output_pairs_df.to_csv(filename, index=False)

    # Build training formats
    output_pairs_df["text"] = (
        "human: Persona: "
        + output_pairs_df["persona"]
        + "\nQuestion: "
        + output_pairs_df["question"]
        + "\n"
        + "bot: "
        + output_pairs_df["answer"]
    )

    # Gemini Data prep: https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini-supervised-tuning-prepare
    # {"contents":[{"role":"user","parts":[{"text":"..."}]},{"role":"model","parts":[{"text":"..."}]}]}
    output_pairs_df["contents"] = output_pairs_df.apply(
        lambda row: [
            {
                "role": "user",
                "parts": [{"text": (f"Persona: {row['persona']}\n\n" f"Question: {row['question']}")}],
            },
            {
                "role": "model",
                "parts": [{"text": row["answer"]}],
            },
        ],
        axis=1,
    )

    # Test train split
    df_train, df_test = train_test_split(output_pairs_df, test_size=0.1, random_state=42)
    df_train[["text"]].to_csv(os.path.join(OUTPUT_FOLDER, "train.csv"), index=False)
    df_test[["text"]].to_csv(os.path.join(OUTPUT_FOLDER, "test.csv"), index=False)

    # Gemini : Max numbers of examples in validation dataset: 256
    df_test = df_test[:256]

    # JSONL
    with open(os.path.join(OUTPUT_FOLDER, "train.jsonl"), "w") as json_file:
        json_file.write(df_train[["contents"]].to_json(orient="records", lines=True))
    with open(os.path.join(OUTPUT_FOLDER, "test.jsonl"), "w") as json_file:
        json_file.write(df_test[["contents"]].to_json(orient="records", lines=True))


def upload():
    print("upload()")

    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    timeout = 300

    data_files = glob.glob(os.path.join(OUTPUT_FOLDER, "*.jsonl")) + glob.glob(os.path.join(OUTPUT_FOLDER, "*.csv"))
    data_files.sort()

    # Upload
    for index, data_file in enumerate(data_files):
        filename = os.path.basename(data_file)
        destination_blob_name = os.path.join("llm-finetune-dataset-small", filename)
        blob = bucket.blob(destination_blob_name)
        print("Uploading file:", data_file, destination_blob_name)
        blob.upload_from_filename(data_file, timeout=timeout)


def main(args=None):
    print("CLI Arguments:", args)

    if args.generate:
        generate()

    if args.prepare:
        prepare()

    if args.upload:
        upload()


if __name__ == "__main__":
    # Generate the inputs arguments parser
    # if you type into the terminal '--help', it will provide the description
    parser = argparse.ArgumentParser(description="CLI")

    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate synthetic Q&A data",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare dataset (CSV + JSONL)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload data to GCS bucket",
    )

    args = parser.parse_args()

    main(args)
