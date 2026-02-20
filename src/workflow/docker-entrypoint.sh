#!/bin/bash

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Environment ready! Virtual environment activated."
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

# Activate virtual environment
echo "Activating virtual environment..."
source /.venv/bin/activate

# Authenticate gcloud using service account
gcloud auth activate-service-account --key-file $GOOGLE_APPLICATION_CREDENTIALS
# Set GCP Project Details
gcloud config set project $GCP_PROJECT

args="$@"
echo $args

# Keep a shell open
#exec /bin/bash

if [[ -z ${args} ]]; 
then
    exec /bin/bash
else
  uv run $args
fi