import os

import pulumi
import pulumi_gcp as gcp

# from pulumi import StackReference, ResourceOptions, Output
import pulumi_kubernetes as k8s

security_config = pulumi.Config("security")
storage_config = pulumi.Config("storage")


def _clean(value):
    """Strip surrounding whitespace off a config string -- see create_cluster._clean."""
    return value.strip() if isinstance(value, str) else value


gsa_email = _clean(security_config.get("gcp_ksa_service_account_email"))
gcs_bucket = _clean(storage_config.get("bucket_name")) or "ac215-project-data"

# The API needs an OpenAI key at runtime -- ChatOpenAI()/OpenAIEmbeddings() read
# OPENAI_API_KEY from the environment. Prefer Pulumi config (encrypted at rest with
# the stack's secrets provider); fall back to the deploying shell's environment,
# which docker-shell.sh forwards from the repo-root .env.
# Note: the vector-db loader job does NOT need this -- jsonl_to_chromadb.py replays
# pre-computed embeddings from the JSONL backup rather than calling OpenAI.
openai_api_key = pulumi.Config("openai").get_secret("api_key") or os.environ.get("OPENAI_API_KEY")

# Braintrust tracing is optional -- api.server._init_tracing() already no-ops cleanly when
# BRAINTRUST_API_KEY is unset, so unlike OPENAI_API_KEY there's nothing to warn about if
# this is missing. Same config/env fallback pattern as above.
braintrust_api_key = pulumi.Config("braintrust").get_secret("api_key") or os.environ.get("BRAINTRUST_API_KEY")
braintrust_project = pulumi.Config("braintrust").get("project") or os.environ.get("BRAINTRUST_PROJECT")


def setup_containers(project, namespace, k8s_provider, ksa_name, app_name, api_ksa=None):
    # Get image references from deploy_images stack
    # For local backend, use: "organization/project/stack"
    images_stack = pulumi.StackReference("organization/deploy-images/dev")
    # Get the image tags (these are arrays, so we take the first element)
    api_service_tag = images_stack.get_output("consult-llm-api-tags")
    frontend_tag = images_stack.get_output("consult-frontend-tags")
    vector_db_cli_tag = images_stack.get_output("consult-vector-db-tags")

    # General persistent storage for application data (5Gi)
    persistent_pvc = k8s.core.v1.PersistentVolumeClaim(
        "persistent-pvc",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="persistent-pvc",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],  # Single pod read/write access
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": "5Gi"},  # Request 5GB of persistent storage
            ),
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
    )

    # Dedicated storage for ChromaDB vector database (10Gi)
    chromadb_pvc = k8s.core.v1.PersistentVolumeClaim(
        "chromadb-pvc",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="chromadb-pvc",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],  # Single pod read/write access
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                # The full PubMed corpus is ~4.1 GiB on disk before ChromaDB's HNSW index
                # and write-ahead log, so 5Gi leaves too little headroom for the loader to
                # finish. PVCs can be grown but never shrunk, so err large -- the extra
                # capacity costs cents per month.
                requests={"storage": "20Gi"},
            ),
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
    )

    # Grant the GSA backing the KSA permission to read from the embeddings bucket
    if gsa_email and gcs_bucket:
        gcp.storage.BucketIAMMember(
            "vector-db-loader-bucket-reader",
            bucket=gcs_bucket,
            role="roles/storage.legacyBucketReader",
            member=pulumi.Output.concat("serviceAccount:", gsa_email),
        )
        gcp.storage.BucketIAMMember(
            "vector-db-loader-object-viewer",
            bucket=gcs_bucket,
            role="roles/storage.objectViewer",
            member=pulumi.Output.concat("serviceAccount:", gsa_email),
        )

    # --- Frontend Deployment ---
    # Creates pods running the frontend container on port 3000
    # ram 1.7 gb
    frontend_deployment = k8s.apps.v1.Deployment(
        "frontend",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="frontend",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={"run": "frontend"},  # Select pods with this label
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={"run": "frontend"},  # Label assigned to pods
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="frontend",
                            image=frontend_tag.apply(
                                lambda tags: tags[0]
                            ),  # Container image (placeholder - needs to be filled)
                            image_pull_policy="IfNotPresent",  # Use cached image if available
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    container_port=80,  # Frontend nginx serves on 80 in the built image
                                    protocol="TCP",
                                )
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "250m", "memory": "2Gi"},
                                limits={"cpu": "500m", "memory": "3Gi"},
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
    )

    frontend_service = k8s.core.v1.Service(
        "frontend-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="frontend",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            type="ClusterIP",  # Internal only - not exposed outside cluster
            ports=[
                k8s.core.v1.ServicePortArgs(
                    port=80,  # Service port
                    target_port=80,  # Container port to forward to
                    protocol="TCP",
                )
            ],
            selector={"run": "frontend"},  # Route traffic to pods with this label
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[frontend_deployment]),
    )

    # vector-db deployment
    vector_db_deployment = k8s.apps.v1.Deployment(
        "vector-db",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="vector-db",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                # Avoid multi-attach errors on the RWO PVC by ensuring only one pod updates at a time.
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0,
                    max_unavailable=1,
                )
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={"run": "vector-db"},
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={"run": "vector-db"},
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        run_as_user=1000,
                        run_as_group=1000,
                        fs_group=1000,
                    ),
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="vector-db",
                            image=vector_db_cli_tag.apply(lambda tags: tags[0]),
                            image_pull_policy="IfNotPresent",
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    container_port=8000,
                                    protocol="TCP",
                                )
                            ],
                            env=[
                                k8s.core.v1.EnvVarArgs(name="IS_PERSISTENT", value="TRUE"),  # Enable data persistence
                                k8s.core.v1.EnvVarArgs(name="ANONYMIZED_TELEMETRY", value="FALSE"),  # Disable telemetry
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_HTTP_TIMEOUT",
                                    value="600",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_SERVER_WORKERS",
                                    value="4",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_DB_PATH",
                                    value="/chroma/chroma",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_SERVER_HOST",
                                    value="0.0.0.0",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_SERVER_PORT",
                                    value="8000",
                                ),
                            ],
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="chromadb-storage",
                                    mount_path="/chroma/chroma",
                                ),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "500m", "memory": "2Gi"},
                                limits={"cpu": "1", "memory": "3Gi"},
                            ),
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="chromadb-storage",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name=chromadb_pvc.metadata.name,  # Mount the 10Gi PVC
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace, chromadb_pvc]),
    )

    # vector-db Service
    vector_db_service = k8s.core.v1.Service(
        "vector-db-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="vector-db",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            type="ClusterIP",  # Internal only
            ports=[
                k8s.core.v1.ServicePortArgs(
                    port=8000,
                    target_port=8000,
                    protocol="TCP",
                )
            ],
            selector={"run": "vector-db"},
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[vector_db_deployment]),
    )

    # Vector DB Loader Job
    vector_db_loader_job = k8s.batch.v1.Job(
        "vector-db-loader",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="vector-db-loader",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.batch.v1.JobSpecArgs(
            backoff_limit=3,  # Retry up to 4 times on failure
            template=k8s.core.v1.PodTemplateSpecArgs(
                spec=k8s.core.v1.PodSpecArgs(
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        run_as_user=1000,
                        run_as_group=1000,
                        fs_group=1000,
                    ),
                    service_account_name=ksa_name,  # Use Workload Identity for GCP access
                    restart_policy="Never",  # Don't restart pod on completion
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="vector-db-loader",
                            image=vector_db_cli_tag.apply(lambda tags: tags[0]),
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "500m", "memory": "2Gi"},
                                limits={"cpu": "1", "memory": "2Gi"},
                            ),
                            env=[
                                k8s.core.v1.EnvVarArgs(name="GCP_PROJECT", value=project),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMADB_HOST",
                                    value="vector-db",
                                ),
                                k8s.core.v1.EnvVarArgs(name="CHROMADB_PORT", value="8000"),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_SERVER_HOST",
                                    value="vector-db",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMA_SERVER_PORT",
                                    value="8000",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMADB_BATCH_SIZE",
                                    value="200",
                                ),
                                # Restore into the collection the API actually queries.
                                k8s.core.v1.EnvVarArgs(
                                    name="CHROMADB_COLLECTION",
                                    value="pubmed_abstract",
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="PROJECT_BUCKET_NAME",
                                    value=gcs_bucket,
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="BACKUP_PREFIX",
                                    value="chromadb_backups/pubmed_abstract",
                                ),
                            ],
                            # jsonl_to_chromadb.py does `from .src.gcs import ...`, so running it
                            # as a bare script fails with "attempted relative import with no known
                            # parent package". It has to be imported as a module, with the parent
                            # of its package directory on PYTHONPATH. In the image, src/models is
                            # copied to /app/src, so the package is `src` and the parent is /app.
                            # No --semantic: that flag reads an "embeddings_semantic" key, but the
                            # backups written by parquet_to_chromadb.py / export_chromadb_backup.py
                            # store the vector under "embedding".
                            # Must run from /app, not /app/src: from inside /app/src the name
                            # `src` resolves to the nested /app/src/src package instead, and the
                            # module isn't found. /.venv is the image's UV_PROJECT_ENVIRONMENT.
                            command=["/bin/sh", "-c"],
                            args=["cd /app && PYTHONPATH=/app /.venv/bin/python -m src.jsonl_to_chromadb"],
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[vector_db_service] + ([api_ksa] if api_ksa else []),
        ),
    )

    # Base environment for the API container.
    api_env = [
        k8s.core.v1.EnvVarArgs(
            name="CHROMADB_HOST",
            value="vector-db",  # ChromaDB service name (DNS)
        ),
        k8s.core.v1.EnvVarArgs(
            name="CHROMADB_PORT",
            value="8000",
        ),
        k8s.core.v1.EnvVarArgs(
            name="GCP_PROJECT",
            value=project,
        ),
        k8s.core.v1.EnvVarArgs(
            name="ROOT_PATH",
            value="/api-service",
        ),
    ]

    # Wire OPENAI_API_KEY in via a Secret when one is available, so it doesn't have
    # to be added by hand after every deploy. Without it the API still starts, but
    # every /api/ask call fails once it tries to reach OpenAI.
    api_extra_deps = []
    if openai_api_key:
        openai_secret = k8s.core.v1.Secret(
            "openai-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="openai-credentials",
                namespace=namespace.metadata.name,
            ),
            string_data={"OPENAI_API_KEY": openai_api_key},
            opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
        )
        api_env.append(
            k8s.core.v1.EnvVarArgs(
                name="OPENAI_API_KEY",
                value_from=k8s.core.v1.EnvVarSourceArgs(
                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                        name=openai_secret.metadata.name,
                        key="OPENAI_API_KEY",
                    )
                ),
            )
        )
        api_extra_deps.append(openai_secret)
    else:
        pulumi.log.warn(
            "No OpenAI key found (openai:api_key config or OPENAI_API_KEY env). "
            "The API will deploy but /api/ask will fail until the key is supplied -- "
            "see Step 7 in src/deployment/README.md."
        )

    # Wire BRAINTRUST_API_KEY in the same way, when available. Unlike OPENAI_API_KEY this
    # is optional -- api.server._init_tracing() no-ops cleanly without it, so no warning here.
    if braintrust_api_key:
        braintrust_secret = k8s.core.v1.Secret(
            "braintrust-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="braintrust-credentials",
                namespace=namespace.metadata.name,
            ),
            string_data={"BRAINTRUST_API_KEY": braintrust_api_key},
            opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
        )
        api_env.append(
            k8s.core.v1.EnvVarArgs(
                name="BRAINTRUST_API_KEY",
                value_from=k8s.core.v1.EnvVarSourceArgs(
                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                        name=braintrust_secret.metadata.name,
                        key="BRAINTRUST_API_KEY",
                    )
                ),
            )
        )
        api_extra_deps.append(braintrust_secret)
        if braintrust_project:
            api_env.append(k8s.core.v1.EnvVarArgs(name="BRAINTRUST_PROJECT", value=braintrust_project))

    # api_service Deployment
    api_deployment = k8s.apps.v1.Deployment(
        "api",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="api",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={"run": "api"},
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={"run": "api"},
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    service_account_name=ksa_name,  # Use KSA for Workload Identity (GCP access)
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        fs_group=1000,
                    ),
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="persistent-vol",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name=persistent_pvc.metadata.name,  # Temporary storage (lost on restart)
                            ),
                        )
                    ],
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="api",
                            image=api_service_tag.apply(
                                lambda tags: tags[0]
                            ),  # API container image (placeholder - needs to be filled)
                            image_pull_policy="IfNotPresent",
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    container_port=8081,  # API server port exposed by uvicorn
                                    protocol="TCP",
                                )
                            ],
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="persistent-vol",
                                    mount_path="/persistent",  # Temporary file storage
                                )
                            ],
                            env=api_env,
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[vector_db_loader_job] + api_extra_deps + ([api_ksa] if api_ksa else []),
        ),
    )

    # api_service Service
    api_service = k8s.core.v1.Service(
        "api-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="api",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            type="ClusterIP",  # Internal only
            ports=[
                k8s.core.v1.ServicePortArgs(
                    port=8081,
                    target_port=8081,
                    protocol="TCP",
                )
            ],
            selector={"run": "api"},
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[api_deployment]),
    )

    return frontend_service, api_service
