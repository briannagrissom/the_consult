# import os
import argparse
import time

# from google.cloud import storage
from google import genai
from google.genai import types

# Setup
GCP_PROJECT = "ac215-ms4"
TRAIN_DATASET = "gs://ac215-ms4-bucket/llm-finetune-dataset-small/train.jsonl"  # Replace with your dataset
VALIDATION_DATASET = "gs://ac215-ms4-bucket/llm-finetune-dataset-small/test.jsonl"  # Replace with your dataset
GCP_LOCATION = "us-central1"
GENERATIVE_SOURCE_MODEL = "gemini-2.0-flash-001"

#############################################################################
#                       Initialize the LLM Client                           #
llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
#############################################################################

# Configuration settings for content generation
generation_config = types.GenerateContentConfig(
    max_output_tokens=3000,
    temperature=0.75,
    top_p=0.95,
)


def train(wait_for_job=False):
    print("train()")

    # Create tuning dataset
    training_dataset = types.TuningDataset(gcs_uri=TRAIN_DATASET)
    validation_dataset = types.TuningDataset(gcs_uri=VALIDATION_DATASET)

    # Create tuning job config
    tuning_config = types.CreateTuningJobConfig(
        epoch_count=1,
        learning_rate_multiplier=1.0,
        adapter_size="ADAPTER_SIZE_FOUR",
        tuned_model_display_name="matling-medical-demo-v3",
        validation_dataset=validation_dataset,
    )

    # Start supervised fine-tuning
    tuning_job = llm_client.tunings.tune(
        base_model=GENERATIVE_SOURCE_MODEL,
        training_dataset=training_dataset,
        config=tuning_config,
    )

    print("Training job started. Monitoring progress...\n")
    print(f"Job name: {tuning_job.name}")

    # Wait and refresh
    time.sleep(60)
    tuning_job = llm_client.tunings.get(name=tuning_job.name)

    if wait_for_job:
        print("Check status of tuning job:")
        print(f"State: {tuning_job.state}")

        completed_states = {
            "JOB_STATE_SUCCEEDED",
            "JOB_STATE_FAILED",
            "JOB_STATE_CANCELLED",
        }

        while tuning_job.state not in completed_states:
            time.sleep(60)
            tuning_job = llm_client.tunings.get(name=tuning_job.name)
            print(f"Job in progress... State: {tuning_job.state}")

    print(f"\nTuned model info: {tuning_job.tuned_model}")
    if tuning_job.tuned_model:
        print(f"Tuned model name: {tuning_job.tuned_model.model}")
        print(f"Tuned model endpoint: {tuning_job.tuned_model.endpoint}")
    print(f"Experiment: {tuning_job.experiment}")

    return tuning_job


def chat(persona: str, question: str):
    print("chat()")

    # IMPORTANT — Update this after tuning finishes
    MODEL_NAME = "projects/650165561090/locations/us-central1/endpoints/7227528634511130624"

    contents = [
        {
            "role": "user",
            "parts": [{"text": f"Persona: {persona}\n\nQuestion: {question}"}],
        }
    ]

    print("contents:", contents)

    response = llm_client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=generation_config,
    )

    print("Fine-tuned LLM Response:", response.text)


def main(args=None):
    print("CLI Arguments:", args)

    if args.train:
        train(wait_for_job=False)

    if args.chat:
        chat(persona=args.persona, question=args.question)


if __name__ == "__main__":
    # Generate the inputs arguments parser
    # if you type into the terminal '--help', it will provide the description
    parser = argparse.ArgumentParser(description="CLI")

    parser.add_argument(
        "--train",
        action="store_true",
        help="Train model",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Chat with model",
    )

    # --- ADD THESE TWO ARGUMENTS (they do NOT replace anything) ---
    parser.add_argument(
        "--persona",
        choices=["Clinician", "Researcher"],
        default="Clinician",
        help="Persona for response style",
    )

    parser.add_argument(
        "--question",
        type=str,
        default="What are the common symptoms of hypertension?",
        help="Question for the model",
    )
    # ---------------------------------------------------------------

    args = parser.parse_args()

    main(args)
