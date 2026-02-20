#!/bin/bash
set -e

# Create output directories
mkdir -p outputs/pubmed_baseline_ftp
mkdir -p outputs/pubmed_baseline_ftp_extract
mkdir -p outputs/pubmed_baseline_ftp_parsed

# Set up Google Cloud authentication
if [ -f "/app/service-account.json" ]; then
    echo "📋 Using service account from /app/service-account.json"
    export GOOGLE_APPLICATION_CREDENTIALS="/app/service-account.json"
    gcloud auth activate-service-account --key-file=/app/service-account.json
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "📋 Using service account from environment variable"
else
    echo "📋 No service account found, using default GCP authentication"
fi

# Activate virtual environment
source /app/.venv/bin/activate

# Step 1: Download PubMed baseline files
echo "📥 Step 1: Downloading PubMed baseline files..."
python get_pm_ftp.py
if [ $? -ne 0 ]; then
    echo "❌ Error downloading files"
    exit 1
fi

# Step 2: Extract downloaded files
echo "📦 Step 2: Extracting downloaded files..."
python extract_pm_ftp.py --xml-dir outputs/pubmed_baseline_ftp
if [ $? -ne 0 ]; then
    echo "❌ Error extracting files"
    exit 1
fi

# Step 3: Parse XML files to pickles
echo "🔄 Step 3: Parsing XML files..."
python parse_pm_ftp.py --xml-dir outputs/pubmed_baseline_ftp_extract --output-dir outputs/pubmed_baseline_ftp_parsed --min-index 400
if [ $? -ne 0 ]; then
    echo "❌ Error parsing files"
    exit 1
fi

# Step 4: Upload filtered data to GCS (or save locally if --local specified)
echo "☁️  Step 4: Processing and uploading data..."
if [ "$SAVE_LOCAL" = "true" ]; then
    echo "💾 Saving locally to outputs/final_dataset/"
    python upload_pm_abstract_ftp.py \
        --data-dir outputs/pubmed_baseline_ftp_parsed \
        --from "${FROM_DATE:-2020-01-01}" \
        --to "${TO_DATE:-2025-12-31}" \
        --local outputs/final_dataset
else
    echo "☁️  Uploading to Google Cloud Storage..."
    python upload_pm_abstract_ftp.py \
        --data-dir outputs/pubmed_baseline_ftp_parsed \
        --from "${FROM_DATE:-2020-01-01}" \
        --to "${TO_DATE:-2025-12-31}"
fi

if [ $? -ne 0 ]; then
    echo "❌ Error processing/uploading data"
    exit 1
fi

echo "✅ Pipeline completed successfully!"

# Keep container running if requested
if [ "$KEEP_RUNNING" = "true" ]; then
    echo "🔄 Keeping container running..."
    exec bash
fi
