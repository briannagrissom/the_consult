#!/bin/bash

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Environment ready! Virtual environment activated."
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

GCP_REGION="${GCP_REGION:-us-central1}"

# Authenticate gcloud using service account
gcloud auth activate-service-account --key-file $GOOGLE_APPLICATION_CREDENTIALS
gcloud config set project $GCP_PROJECT --quiet
# Log in to Artifact Registry. This targets the regional host that deploy_images
# actually pushes to ({region}-docker.pkg.dev), not the us-docker.pkg.dev multi-region.
# docker_config.json is bind-mounted, so gcloud's atomic rename onto it fails with
# "Device or resource busy" -- harmless, since that file already ships the needed
# credHelper entry, so the write is only a no-op refresh. Don't let it abort the script.
gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet || \
    echo "Note: could not rewrite /root/.docker/config.json (it is bind-mounted); using its existing credHelpers."
# Check if the bucket exists
if ! gsutil ls -b $PULUMI_BUCKET >/dev/null 2>&1; then
    echo "Bucket does not exist. Creating..."
    # -l is required: without it gsutil defaults to the multi-region "US", which a
    # constraints/gcp.resourceLocations org policy restricted to specific regions
    # will reject with "412 ... violates constraint".
    gsutil mb -p $GCP_PROJECT -l $GCP_REGION $PULUMI_BUCKET
else
    echo "Bucket already exists. Skipping creation."
fi

echo "Logging into Pulumi using GCS bucket: $PULUMI_BUCKET"
pulumi login $PULUMI_BUCKET

# List available stacks
echo "Available Pulumi stacks in GCS:"
gsutil ls $PULUMI_BUCKET/.pulumi/stacks/  || echo "No stacks found."

# Run Bash for interactive mode
/bin/bash