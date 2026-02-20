import pulumi
import pulumi_gcp as gcp

# from pulumi import StackReference, ResourceOptions, Output
import pulumi_kubernetes as k8s

security_config = pulumi.Config("security")
storage_config = pulumi.Config("storage")

gsa_email = security_config.get("gcp_ksa_service_account_email")
gcs_bucket = storage_config.get("bucket_name") or "pubmed-bucket-ac215"


def setup_containers(project, namespace, k8s_provider, ksa_name, app_name):
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
                requests={"storage": "5Gi"},  # Request 5GB for vector embeddings
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
                            ],
                            # Run the loader via Python so it resolves correctly in the image
                            command=["/bin/sh", "-c"],
                            args=["uv run /app/src/jsonl_to_chromadb.py --semantic"],
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[vector_db_service],
        ),
    )

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
                            env=[
                                # k8s.core.v1.EnvVarArgs(
                                #     name="GCS_BUCKET_NAME",
                                #     value="cheese-app-models",  # GCS bucket for ML models
                                # ),
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
                            ],
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[vector_db_loader_job]),
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
