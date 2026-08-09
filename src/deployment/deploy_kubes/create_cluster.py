import pulumi
import pulumi_gcp as gcp
from pulumi import Output, ResourceOptions
import pulumi_kubernetes as k8s

# import pulumi_command as command
import yaml

base_config = pulumi.Config()


def _clean(value):
    """Strip surrounding whitespace off a config string.

    `pulumi config set` stores whatever it is given, and a value pasted at its
    interactive `value:` prompt easily picks up a trailing space. GCP then rejects
    the principal with a confusing "Verify if principal exists and is valid" 400
    that gives no hint whitespace is involved.
    """
    return value.strip() if isinstance(value, str) else value


service_account_email = _clean(pulumi.Config("security").get("gcp_service_account_email"))
ksa_service_account_email = _clean(pulumi.Config("security").get("gcp_ksa_service_account_email"))
initial_node_count = 1
# Overridable so a zonal capacity shortage is a config change, not a code edit:
#   pulumi config set compute:machine_type e2-standard-4
#   pulumi config set compute:node_zone   us-central1-f
# e2-standard-2 (2 vCPU / 8 GB, same shape as the n2d it replaced) is the most
# widely stocked family; n2d-standard-2 in us-central1-a failed with GCE_STOCKOUT.
# Keep node_zone to ONE zone: this is a regional cluster, so node counts are
# per-zone and listing three zones would triple the node count and the bill.
compute_config = pulumi.Config("compute")
machine_type = _clean(compute_config.get("machine_type")) or "e2-standard-2"
node_zone = _clean(compute_config.get("node_zone")) or "us-central1-c"
machine_disk_size = 50


def create_cluster(project, region, network, subnet, app_name):
    # Create GKE cluster with private nodes, workload identity enabled, and no default node pool
    cluster = gcp.container.Cluster(
        f"{app_name}-cluster",
        name=f"{app_name}-cluster",
        description="GKE cluster",
        location=region,
        deletion_protection=False,
        network=network.name,
        subnetwork=subnet.name,
        remove_default_node_pool=True,  # Remove default pool to use custom node pools
        initial_node_count=1,
        private_cluster_config={
            "enable_private_nodes": True,  # Nodes use private IPs only
            "enable_private_endpoint": False,  # Control plane accessible via public endpoint
            "master_ipv4_cidr_block": "172.0.0.0/28",  # CIDR for GKE control plane
        },
        workload_identity_config={
            "workload_pool": f"{project}.svc.id.goog"
        },  # Enable Workload Identity for secure service account access
        gateway_api_config={
            "channel": "CHANNEL_STANDARD",  # Enable Gateway API for advanced ingress
        },
    )

    # Create custom node pool with autoscaling, auto-repair, and standard GCP service permissions
    node_pool = gcp.container.NodePool(
        f"{app_name}-pool",
        cluster=cluster.name,
        location=region,
        initial_node_count=1,
        node_config=gcp.container.NodePoolNodeConfigArgs(
            service_account=service_account_email,
            machine_type=machine_type,
            image_type="cos_containerd",  # Container-Optimized OS with containerd runtime
            disk_size_gb=machine_disk_size,
            oauth_scopes=[  # OAuth scopes for node service account permissions
                "https://www.googleapis.com/auth/devstorage.read_only",  # Read from GCS
                "https://www.googleapis.com/auth/logging.write",  # Write logs to Cloud Logging
                "https://www.googleapis.com/auth/monitoring",  # Send metrics to Cloud Monitoring
                "https://www.googleapis.com/auth/servicecontrol",  # Service control access
                "https://www.googleapis.com/auth/service.management.readonly",  # Read service management
                "https://www.googleapis.com/auth/trace.append",  # Write traces to Cloud Trace
            ],
        ),
        autoscaling=gcp.container.NodePoolAutoscalingArgs(
            min_node_count=1,  # Minimum nodes to keep running
            max_node_count=2,  # Maximum nodes for scale-up
        ),
        management=gcp.container.NodePoolManagementArgs(
            auto_repair=True,  # Automatically repair unhealthy nodes
            auto_upgrade=True,  # Automatically upgrade to new GKE versions
        ),
        node_locations=[node_zone],
    )

    # -----------------------------
    # Kubeconfig for k8s provider
    # -----------------------------
    k8s_info = pulumi.Output.all(cluster.name, cluster.endpoint, cluster.master_auth)

    def make_kubeconfig(info):
        cluster_name, endpoint, master_auth = info
        context_name = f"{project}_{region}_{cluster_name}"

        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": context_name,
                    "cluster": {
                        "certificate-authority-data": master_auth["cluster_ca_certificate"],
                        "server": f"https://{endpoint}",
                    },
                }
            ],
            "contexts": [
                {
                    "name": context_name,
                    "context": {"cluster": context_name, "user": context_name},
                }
            ],
            "current-context": context_name,
            "users": [
                {
                    "name": context_name,
                    "user": {
                        "exec": {
                            "apiVersion": "client.authentication.k8s.io/v1beta1",
                            "command": "gke-gcloud-auth-plugin",
                            "installHint": "Install gke-gcloud-auth-plugin for use with kubectl",
                            "provideClusterInfo": True,
                            "interactiveMode": "Never",
                        }
                    },
                }
            ],
        }

        # Generate YAML
        result = yaml.dump(kubeconfig, default_flow_style=False)

        # Debug
        # print("=" * 80)
        # print("Generated kubeconfig:")
        # print(result)
        # print("=" * 80)

        return result

    cluster_kubeconfig = k8s_info.apply(make_kubeconfig)

    # Create Kubernetes provider using GKE cluster credentials to deploy K8s resources
    k8s_provider = k8s.Provider(
        "gke_k8s_v2",
        kubeconfig=cluster_kubeconfig,  # Use the kubeconfig generated from the GKE cluster
        opts=ResourceOptions(depends_on=[node_pool]),  # Wait for node pool to be ready
    )

    # Create Kubernetes namespace for application deployments
    namespace = k8s.core.v1.Namespace(
        f"{app_name}-namespace",
        metadata={"name": f"{app_name}-namespace"},
        opts=ResourceOptions(provider=k8s_provider),
    )

    # --- Kubernetes Service Account (KSA) with Workload Identity annotation ---
    # KSA = Kubernetes Service Account - an identity used by pods running in K8s to authenticate
    # This allows pods to securely access GCP services without embedding credentials
    ksa_name = "api-ksa"

    # This must actually exist: the api Deployment and the vector-db-loader Job both set
    # service_account_name=ksa_name, and Kubernetes refuses to create a pod whose service
    # account is missing ("error looking up service account ...: serviceaccount not found"),
    # which leaves the Job stuck in FailedCreate forever.
    api_ksa = k8s.core.v1.ServiceAccount(
        "api-ksa",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=ksa_name,
            namespace=namespace.metadata["name"],
            annotations={
                # Critical: map KSA -> GSA (Google Service Account) for Workload Identity
                "iam.gke.io/gcp-service-account": ksa_service_account_email,
            },
        ),
        opts=ResourceOptions(provider=k8s_provider, depends_on=[namespace]),
    )

    # --- Bind KSA identity to the GSA (Workload Identity user) ---
    # Without this the KSA exists but cannot impersonate the GSA, so pods get no GCP
    # credentials and the loader cannot read the backup out of GCS.
    # member format: serviceAccount:<PROJECT_ID>.svc.id.goog[<namespace>/<ksa_name>]
    project_id = gcp.config.project
    wi_member = Output.concat(
        "serviceAccount:",
        project_id,
        ".svc.id.goog[",
        namespace.metadata["name"],
        "/",
        ksa_name,
        "]",
    )
    gsa_full_id = Output.concat("projects/", project_id, "/serviceAccounts/", ksa_service_account_email)

    gcp.serviceaccount.IAMMember(
        "api-gsa-wi-user",
        service_account_id=gsa_full_id,
        role="roles/iam.workloadIdentityUser",
        member=wi_member,
        opts=ResourceOptions(depends_on=[api_ksa]),
    )

    # Connect to the cluster
    # connect_k8s_command = command.local.Command(
    #     "connect-k8s-command",
    #     create=Output.concat(
    #         "gcloud container clusters get-credentials ", cluster.name, f" --zone {region} --project {project}"
    #     ),
    # )

    return cluster, namespace, k8s_provider, ksa_name, api_ksa
