# Deploying The Consult to GCP

End-to-end guide to deploying this app to Google Cloud from a clean slate: no
project, no cluster, no credentials. Everything runs through Pulumi from inside
a container that already has `gcloud`, `docker`, `kubectl`, `helm`, and `pulumi`
installed, so the only tools you need locally are Docker and `gcloud`.

## What gets deployed

Two Pulumi programs, run in order:

| Program | Creates |
|---|---|
| `deploy_images/` | An Artifact Registry repo (`the-consult-repository`), then builds and pushes 4 images: `consult-llm-api`, `consult-frontend`, `consult-vector-db`, `consult-app-workflow` |
| `deploy_kubes/` | VPC network + subnet + Cloud Router + Cloud NAT, a regional GKE cluster with an autoscaling node pool, the app's Kubernetes Deployments/Services (frontend, API, ChromaDB), and an nginx ingress + external IP |

`deploy_kubes` reads the image tags from `deploy_images`' stack outputs, so
**`deploy_images` must succeed first.**

## ⚠️ Cost

`deploy_images` is cheap — Artifact Registry storage only (~$0.10/GB/month).

`deploy_kubes` provisions billable infrastructure that runs until you destroy it:

| Resource | Spec | Approx. us-central1 cost |
|---|---|---|
| GKE control plane | regional | ~$0.10/hr (~$73/mo) |
| Node pool | `n2d-standard-2`, autoscale 1→2 | ~$0.07/hr per node (~$51/mo) |
| Boot disks | 50 GB per node | ~$5/mo per node |
| Cloud NAT | 1 gateway | ~$0.044/hr (~$32/mo) + data |
| External IP + load balancer | | ~$18–25/mo |

**Rough total: $0.20–0.30/hour, or ~$150–220/month if left running.** These are
estimates — check the [pricing calculator](https://cloud.google.com/products/calculator)
for authoritative figures. Set a [budget alert](https://console.cloud.google.com/billing/budgets)
before you start, and see [Tearing it down](#tearing-it-down) when you're finished.

---

## Prerequisites

- **Docker** running locally
- **[gcloud CLI](https://cloud.google.com/sdk/docs/install)**, authenticated: `gcloud auth login`
- A **GCP account with billing enabled**
- An **OpenAI API key** (the app's LLM and embeddings both use it)

---

## Step 1 — Create and configure a GCP project

```bash
export PROJECT_ID=your-project-id          # must be globally unique
export REGION=us-central1

gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID
```

Link a billing account (find yours with `gcloud billing accounts list`):

```bash
gcloud billing projects link $PROJECT_ID --billing-account=XXXXXX-XXXXXX-XXXXXX
```

Enable the required APIs. **All five are mandatory** — `container` and `compute`
are the ones most often forgotten, and their absence causes a `403 ... API has
not been used in project` failure partway through `deploy_kubes`:

```bash
gcloud services enable \
  container.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com \
  --project=$PROJECT_ID
```

> Add `aiplatform.googleapis.com` too if you plan to run the Vertex AI ML
> workflow in `src/workflow`.

## Step 2 — Create the deployment service account

```bash
export SA_NAME=consult-app-local
export SA_EMAIL=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com

gcloud iam service-accounts create $SA_NAME \
  --display-name="Consult App Deployment" \
  --project=$PROJECT_ID
```

Grant the roles Pulumi needs. Each maps to a resource type the programs create:

```bash
for ROLE in \
  roles/artifactregistry.admin \
  roles/container.admin \
  roles/compute.admin \
  roles/storage.admin \
  roles/iam.serviceAccountAdmin \
  roles/iam.serviceAccountUser
do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE"
done
```

| Role | Needed for |
|---|---|
| `artifactregistry.admin` | Creating the repo and pushing images |
| `container.admin` | Creating the GKE cluster and node pool |
| `compute.admin` | VPC, subnet, router, NAT, global static IP |
| `storage.admin` | Pulumi state bucket + granting the app read access to your data bucket |
| `iam.serviceAccountAdmin` | Workload Identity binding (GSA ↔ KSA) |
| `iam.serviceAccountUser` | Attaching the service account to node pool VMs |

> `compute.admin` + `container.admin` + `iam.serviceAccountAdmin` on a single
> key is broad access. That's typical for a course/demo project, but treat the
> key file accordingly — never commit it.

Download the key to the repo's `secrets/` folder (which is gitignored):

```bash
cd /path/to/the_consult
mkdir -p secrets
gcloud iam service-accounts keys create secrets/consult-app-local.json \
  --iam-account=$SA_EMAIL

# The deployment container mounts secrets/ and expects an .ssh dir to exist
mkdir -p secrets/.ssh
```

## Step 3 — Point the repo at your project

Edit [`docker-shell.sh`](docker-shell.sh) and set:

```bash
export GCP_PROJECT="your-project-id"
export GCP_REGION="us-central1"
export GCP_ZONE="us-central1-a"
export GOOGLE_APPLICATION_CREDENTIALS=/secrets/consult-app-local.json
```

`GOOGLE_APPLICATION_CREDENTIALS` is a path *inside* the container — `secrets/`
is mounted to `/secrets`, so the filename must match the key you created above.

If you named your key something else, update that line to match.

## Step 4 — Launch the deployment container

```bash
cd src/deployment
./docker-shell.sh
```

On startup the container automatically authenticates gcloud with your key, sets
the project, configures Docker for Artifact Registry, **creates the Pulumi state
bucket** (`gs://<project>-pulumi-state-bucket`) if it doesn't exist, and runs
`pulumi login` against it. You'll land in a bash shell inside `/app`.

Everything below runs **inside this container**.

## Step 5 — Build and push the images

```bash
cd deploy_images

pulumi stack init dev        # skip if the stack already exists
pulumi config set gcp:project your-project-id
pulumi config set gcp:region us-central1

pulumi up --stack dev --refresh -y
```

This builds four images for `linux/amd64` and pushes them. Expect 10–30 minutes
on a first run; the frontend and API images are large. Image tags are timestamped
and exported as stack outputs for the next step.

Verify:

```bash
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/your-project-id/the-consult-repository
```

## Step 6 — Deploy the cluster

> This is the step that starts incurring hourly cost.

```bash
cd ../deploy_kubes

pulumi stack init dev
pulumi config set gcp:project your-project-id
pulumi config set gcp:region us-central1
pulumi config set security:gcp_service_account_email $SA_EMAIL
pulumi config set security:gcp_ksa_service_account_email $SA_EMAIL
pulumi config set storage:bucket_name your-embeddings-bucket

pulumi up --stack dev --refresh -y
```

| Config key | Meaning |
|---|---|
| `security:gcp_service_account_email` | GSA attached to the node pool |
| `security:gcp_ksa_service_account_email` | GSA bound to the Kubernetes SA via Workload Identity, so pods reach GCS without a mounted key |
| `storage:bucket_name` | GCS bucket holding your embeddings/Parquet data; the app is granted read access to it |

Cluster creation takes roughly 10–15 minutes. When it finishes, Pulumi prints
the app URL:

```bash
pulumi stack output app_url
```

The load balancer can take a few more minutes to start serving after `pulumi up`
returns. Deploys are HTTP by default — set `setupSSL = True` in
[`deploy_kubes/__main__.py`](deploy_kubes/__main__.py) to use the SSL ingress path.

## Step 7 — Provide the OpenAI API key

**This is a known gap: the Pulumi code does not wire `OPENAI_API_KEY` into the
deployed pods.** The API container starts fine, but any `/api/ask` request fails
because `ChatOpenAI()` and `OpenAIEmbeddings()` fall back to that environment
variable and find nothing.

Until it's wired into `setup_containers.py`, add it manually:

```bash
gcloud container clusters get-credentials the-consult-app-cluster \
  --region us-central1 --project your-project-id

NS=$(pulumi stack output namespace)

kubectl create secret generic openai-credentials \
  --from-literal=OPENAI_API_KEY=sk-... -n $NS

kubectl set env deployment/api -n $NS \
  --from=secret/openai-credentials
```

> The Kubernetes *Deployment* is named `api`; the *Service* in front of it is
> `api-service`. `kubectl set env` needs the Deployment.

Confirm the rollout:

```bash
kubectl get pods -n $NS -w
```

## Step 8 — Load data into the vector database

The cluster runs an empty ChromaDB. Populate it with the `consult-vector-db`
image, or from your machine following [`src/models/README.md`](../models/README.md).
The parquet files it ingests are produced by
[`src/datapipeline`](../datapipeline/README.md).

---

## Verifying the deployment

```bash
kubectl get pods,svc,ingress -n $NS      # everything Running / an ingress IP assigned

# The ingress routes / to the frontend and /api-service to the API,
# so the API's health endpoint sits behind that prefix:
curl http://$(pulumi stack output ip_address)/api-service/healthz
```

Then open the URL from `pulumi stack output app_url` in a browser.

## Tearing it down

**Do this when you're done — the cluster bills hourly until it's gone.**

```bash
cd deploy_kubes && pulumi destroy --stack dev -y
```

`deletion_protection` is disabled on the cluster, so this removes it cleanly.

Images are declared with `retain_on_delete=True`, so destroying the
`deploy_images` stack leaves them in Artifact Registry (storage cost only).
Remove them and the state bucket manually if you want a full cleanup:

```bash
cd ../deploy_images && pulumi destroy --stack dev -y
gcloud artifacts repositories delete the-consult-repository --location=us-central1
gsutil rm -r gs://your-project-id-pulumi-state-bucket
```

---

## Troubleshooting

**`403 ... API has not been used in project`** — an API from Step 1 isn't
enabled. Enable it and re-run; propagation takes a minute or two.

**`Permission denied` / `caller does not have permission`** — a missing role
from Step 2. Check what the SA actually has:

```bash
gcloud projects get-iam-policy your-project-id \
  --flatten="bindings[].members" \
  --filter="bindings.members:$SA_EMAIL" \
  --format="value(bindings.role)"
```

**`deploy_kubes` can't find image tags** — `deploy_images` hasn't been deployed,
or its stack name doesn't match. `setup_containers.py` references
`organization/deploy-images/dev`; with a self-managed (GCS) backend, `organization`
is Pulumi's default org name, so the stack must be named exactly `dev`.

**Pulumi state bucket errors** — the entrypoint creates it automatically, but
that needs `storage.admin`. Confirm `PULUMI_BUCKET` in `docker-shell.sh` includes
the `gs://` scheme.

**Frontend loads but requests fail** — usually Step 7. Check the API pod's logs:
`kubectl logs deployment/api -n $NS`.

## Known gaps

- `OPENAI_API_KEY` isn't provisioned by Pulumi (Step 7 is a manual workaround).
- The `deploy_kubes` stack has no committed `Pulumi.dev.yaml`, so its config must
  be set by hand on first run. `deploy_images` does ship one.
- SSL is off by default; the app is served over plain HTTP.
