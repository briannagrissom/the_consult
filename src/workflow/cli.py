"""
Module that contains the command line app.

Typical usage example from command line:
        python cli.py
"""

import os
import argparse
import random
import string
import json
# import sys
from google.cloud import storage
from kfp import dsl
from kfp import compiler
import google.cloud.aiplatform as aip
from model import model_training as model_training_job # model_deploy as model_deploy_job

# Test 2
GCP_PROJECT = os.environ["GCP_PROJECT"]
GCS_BUCKET_NAME = os.environ["GCS_BUCKET_NAME"]
BUCKET_URI = f"gs://{GCS_BUCKET_NAME}"
PIPELINE_ROOT = f"{BUCKET_URI}/pipeline_root/root"
GCS_SERVICE_ACCOUNT = os.environ["GCS_SERVICE_ACCOUNT"]
GCS_PACKAGE_URI = os.environ["GCS_PACKAGE_URI"]
GCP_REGION = os.environ["GCP_REGION"]
PULUMI_BUCKET = os.environ["PULUMI_BUCKET"]
STACK_PATH = ".pulumi/stacks/deploy-images/dev.json"


def get_pulumi_stack_outputs(bucket_name: str, stack_path: str, output_keys: list[str] | None = None) -> dict:
    # Initialize GCS client
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(stack_path)

    # Download and parse JSON
    stack_content = blob.download_as_text()
    stack_data = json.loads(stack_content)

    # Extract outputs from the stack resource
    outputs = {}
    for resource in stack_data.get("checkpoint", {}).get("latest", {}).get("resources", []):
        if resource.get("type") == "pulumi:pulumi:Stack":
            outputs = resource.get("outputs", {})
            break

    # Filter to requested keys if specified
    if output_keys:
        outputs = {k: v for k, v in outputs.items() if k in output_keys}

    return outputs


def generate_uuid(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# Successful
def data_collector():
    print("data_collector()")

    image_tags = get_pulumi_stack_outputs(PULUMI_BUCKET, STACK_PATH, ["consult-app-workflow-tags"])
    print(image_tags)
    workflow_image = image_tags["consult-app-workflow-tags"][0]

    @dsl.container_component
    def data_collector():
        container_spec = dsl.ContainerSpec(
            image=workflow_image,
            command=[],
            args=[
                "cli.py",
                "--search",
                "--nums 10",
                "--query consult message",
                f"--bucket {GCS_BUCKET_NAME}",
            ],
        )
        return container_spec

    # Define a Pipeline
    @dsl.pipeline
    def data_collector_pipeline():
        data_collector()

    # Build yaml file for pipeline
    compiler.Compiler().compile(data_collector_pipeline, package_path="data_collector.yaml")

    # Submit job to Vertex AI
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "consult-app-data-collector-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="data_collector.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT, sync=False)


def data_processor():
    print("data_processor()")

    image_tags = get_pulumi_stack_outputs(PULUMI_BUCKET, STACK_PATH, ["consult-app-workflow-tags"])
    print(image_tags)
    workflow_image = image_tags["consult-app-workflow-tags"][0]

    @dsl.container_component
    def data_processor():
        container_spec = dsl.ContainerSpec(
            image=workflow_image,
            command=[],
            args=[
                "cli.py",
                "--clean",
                f"--bucket {GCS_BUCKET_NAME}",
            ],
        )
        return container_spec

    # Define a Pipeline
    @dsl.pipeline
    def data_processor_pipeline():
        data_processor()

    # Build yaml file for pipeline
    compiler.Compiler().compile(data_processor_pipeline, package_path="data_processor.yaml")

    # Submit job to Vertex AI
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "consult-app-data-processor-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="data_processor.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT, sync=False)


def model_training():
    print("model_training()")

    # Define a Pipeline
    @dsl.pipeline
    def model_training_pipeline():
        model_training_job(
            project=GCP_PROJECT,
            location=GCP_REGION,
            staging_bucket=GCS_PACKAGE_URI,
            bucket_name=GCS_BUCKET_NAME,
        )

    # Build yaml file for pipeline
    compiler.Compiler().compile(model_training_pipeline, package_path="model_training.yaml")

    # Submit job to Vertex AI
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "consult-app-model-training-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="model_training.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT, sync=False)


def model_deploy():
    print("model_deploy()")

    # Define a Pipeline
    @dsl.pipeline
    def model_deploy_pipeline():
        model_deploy(
            bucket_name=GCS_BUCKET_NAME,
        )

    # Build yaml file for pipeline
    compiler.Compiler().compile(model_deploy_pipeline, package_path="model_deploy.yaml")

    # Submit job to Vertex AI
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "consult-app-model-deploy-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="model_deploy.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT, sync=False)


def pipeline():
    print("pipeline()")

    image_tags = get_pulumi_stack_outputs(
        PULUMI_BUCKET,
        STACK_PATH,
        ["consult-app-workflow-tags"],
    )
    print(image_tags)

    # Define a Container Component for data collector
    @dsl.container_component
    def data_collector():
        container_spec = dsl.ContainerSpec(
            image=image_tags["consult-app-workflow-tags"][0],
            command=[],
            args=[
                "cli.py",
                "--search",
                "--nums 50",
                "--query consult message",
                f"--bucket {GCS_BUCKET_NAME}",
            ],
        )
        return container_spec

    # Define a Container Component for data processor
    @dsl.container_component
    def data_processor():
        container_spec = dsl.ContainerSpec(
            image=image_tags["consult-app-workflow-tags"][0],
            command=[],
            args=[
                "cli.py",
                "--clean",
                f"--bucket {GCS_BUCKET_NAME}",
            ],
        )
        return container_spec

    # Define a Pipeline
    @dsl.pipeline
    def ml_pipeline():
        # Data Collector
        data_collector_task = (
            data_collector().set_display_name("Data Collector").set_cpu_limit("500m").set_memory_limit("2G")
        )
        # Data Processor
        data_processor_task = data_processor().set_display_name("Data Processor").after(data_collector_task)
        # Model Training
        model_training_task = (
            model_training_job(
                project=GCP_PROJECT,
                location=GCP_REGION,
                staging_bucket=GCS_PACKAGE_URI,
                bucket_name=GCS_BUCKET_NAME,
                epochs=15,
                batch_size=16,
                model_name="mobilenetv2",
                train_base=False,
            )
            .set_display_name("Model Training")
            .after(data_processor_task)
        )
        # Model Deployment
        model_deploy_task = (
            model_deploy_job(
                bucket_name=GCS_BUCKET_NAME,
            )
            .set_display_name("Model Deploy")
            .after(model_training_task)
        )

    # Build yaml file for pipeline
    compiler.Compiler().compile(ml_pipeline, package_path="pipeline.yaml")

    # Submit job to Vertex AI
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "consult-app-pipeline-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="pipeline.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT, sync=False)


def sample_pipeline():
    print("sample_pipeline()")

    # Define Component
    @dsl.component
    def square(x: float) -> float:
        return x**2

    # Define Component
    @dsl.component
    def add(x: float, y: float) -> float:
        return x + y

    # Define Component
    @dsl.component
    def square_root(x: float) -> float:
        return x**0.5

    # Define a Pipeline
    @dsl.pipeline
    def sample_pipeline(a: float = 3.0, b: float = 4.0) -> float:
        a_sq_task = square(x=a)
        b_sq_task = square(x=b)
        sum_task = add(x=a_sq_task.output, y=b_sq_task.output)
        return square_root(x=sum_task.output).output

    # Build yaml file for pipeline
    compiler.Compiler().compile(sample_pipeline, package_path="sample-pipeline1.yaml")

    # Submit job to Vertex AI, fix service account
    aip.init(project=GCP_PROJECT, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "sample-pipeline-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="sample-pipeline1.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def main(args=None):
    print("CLI Arguments:", args)

    if args.data_collector:
        data_collector()

    if args.data_processor:
        print("Data Processor")
        data_processor()

    if args.model_training:
        print("Model Training")
        model_training()

    if args.model_deploy:
        print("Model Deploy")
        model_deploy()

    if args.pipeline:
        pipeline()

    if args.sample:
        print("Sample Pipeline")
        sample_pipeline()


if __name__ == "__main__":
    # Generate the inputs arguments parser
    # if you type into the terminal 'python cli.py --help', it will provide the description
    parser = argparse.ArgumentParser(description="Workflow CLI")

    parser.add_argument(
        "--data_collector",
        action="store_true",
        help="Run just the Data Collector",
    )
    parser.add_argument(
        "--data_processor",
        action="store_true",
        help="Run just the Data Processor",
    )
    parser.add_argument(
        "--model_training",
        action="store_true",
        help="Run just Model Training",
    )
    parser.add_argument(
        "--model_deploy",
        action="store_true",
        help="Run just Model Deployment",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Consult App Pipeline",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Sample Pipeline 1",
    )

    args = parser.parse_args()

    main(args)
