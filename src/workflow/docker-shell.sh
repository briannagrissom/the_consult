#!/bin/bash

# set -e

export IMAGE_NAME="consult-app-workflow"
export BASE_DIR=$(pwd)
export SECRETS_DIR=$(pwd)/../../../secrets/
export SSH_DIR=$(pwd)/../../secrets/
export GCP_PROJECT="vm1-test-473912" # Change to your GCP Project ID
export GCS_BUCKET_NAME="consult-app-ml-workflow-demo"
export GCS_SERVICE_ACCOUNT="ml-workflow@vm1-test-473912.iam.gserviceaccount.com" # Change GCP Project ID
export GCP_REGION="us-central1"
export GCS_PACKAGE_URI="gs://consult-app-trainer-code"
export GOOGLE_APPLICATION_CREDENTIALS=/secrets/deployment.json
export PULUMI_BUCKET="gs://$GCP_PROJECT-pulumi-state-bucket"

# Build the image based on the Dockerfile
#docker build -t $IMAGE_NAME -f Dockerfile .
docker build -t $IMAGE_NAME --platform=linux/amd64 -f Dockerfile .


# Run Container
docker run --rm --name $IMAGE_NAME -ti \
-v /var/run/docker.sock:/var/run/docker.sock \
-v "$BASE_DIR":/app \
-v "$SECRETS_DIR":/secrets \
-v "$BASE_DIR/../data-collector":/data-collector \
-v "$BASE_DIR/../data-processor":/data-processor \
-e GOOGLE_APPLICATION_CREDENTIALS=/secrets/ml-workflow.json \
-e GCP_PROJECT=$GCP_PROJECT \
-e GCS_BUCKET_NAME=$GCS_BUCKET_NAME \
-e GCS_SERVICE_ACCOUNT=$GCS_SERVICE_ACCOUNT \
-e GCP_REGION=$GCP_REGION \
-e GCS_PACKAGE_URI=$GCS_PACKAGE_URI \
-e WANDB_KEY=$WANDB_KEY \
-e PULUMI_BUCKET=$PULUMI_BUCKET \
$IMAGE_NAME

