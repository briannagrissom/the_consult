# import os
import pulumi
import pulumi_docker_build as docker_build
from pulumi_gcp import artifactregistry
from pulumi import CustomTimeouts
import datetime

# 🔧 Get project info
gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
location = gcp_config.require("region")
# Old: location = os.environ["GCP_REGION"]

# 🕒 Timestamp for tagging
timestamp_tag = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
repository_name = "the-consult-repository"
registry_url = f"{location}-docker.pkg.dev/{project}/{repository_name}"

repo = artifactregistry.Repository(
    repository_name,
    format="DOCKER",
    location=location,
    repository_id=repository_name,
)


def _gha_cache(scope: str):
    """Cache layers via GitHub Actions' cache backend, so an app-code-only change reuses
    the dependency-install layer instead of reinstalling everything on every deploy.
    Scoped per image name so the 4 builds below don't share/overwrite each other's cache.
    Needs ACTIONS_CACHE_URL and ACTIONS_RUNTIME_TOKEN in the environment -- see the
    "Expose GitHub Actions cache" and "Run Deploy App" steps in app-ci-cd-gcp.yml."""
    cache_from = [docker_build.CacheFromArgs(gha=docker_build.CacheFromGitHubActionsArgs(scope=scope))]
    cache_to_gha = docker_build.CacheToGitHubActionsArgs(scope=scope, mode=docker_build.CacheMode.MAX)
    cache_to = [docker_build.CacheToArgs(gha=cache_to_gha)]
    return cache_from, cache_to


# Docker Build + Push -> API Service
# consult-llm-api service
image_config = {"image_name": "consult-llm-api", "context_path": "../../llm-api", "dockerfile": "Dockerfile"}
cache_from, cache_to = _gha_cache(image_config["image_name"])
api_service_image = docker_build.Image(
    f"build-{image_config['image_name']}",
    tags=[pulumi.Output.concat(registry_url, "/", image_config["image_name"], ":", timestamp_tag)],
    context=docker_build.BuildContextArgs(location=image_config["context_path"]),
    dockerfile={"location": f"{image_config['context_path']}/{image_config['dockerfile']}"},
    platforms=[docker_build.Platform.LINUX_AMD64],
    push=True,
    cache_from=cache_from,
    cache_to=cache_to,
    opts=pulumi.ResourceOptions(custom_timeouts=CustomTimeouts(create="30m"), retain_on_delete=True, depends_on=[repo]),
)
# Export references to stack
pulumi.export("consult-llm-api-ref", api_service_image.ref)
pulumi.export("consult-llm-api-tags", api_service_image.tags)

# Docker Build + Push -> Frontend
image_config = {"image_name": "consult-frontend", "context_path": "../../frontend", "dockerfile": "Dockerfile"}
cache_from, cache_to = _gha_cache(image_config["image_name"])
frontend_image = docker_build.Image(
    f"build-{image_config['image_name']}",
    tags=[pulumi.Output.concat(registry_url, "/", image_config["image_name"], ":", timestamp_tag)],
    context=docker_build.BuildContextArgs(location=image_config["context_path"]),
    dockerfile={"location": f"{image_config['context_path']}/{image_config['dockerfile']}"},
    build_args={"VITE_API_BASE_URL": "/api-service"},
    platforms=[docker_build.Platform.LINUX_AMD64],
    push=True,
    cache_from=cache_from,
    cache_to=cache_to,
    opts=pulumi.ResourceOptions(custom_timeouts=CustomTimeouts(create="30m"), retain_on_delete=True, depends_on=[repo]),
)
pulumi.export("consult-frontend-ref", frontend_image.ref)
pulumi.export("consult-frontend-tags", frontend_image.tags)

# Docker Build + Push -> vector-db-cli
image_config = {"image_name": "consult-vector-db", "context_path": "../../models", "dockerfile": "Dockerfile"}
cache_from, cache_to = _gha_cache(image_config["image_name"])
frontend_image = docker_build.Image(
    f"build-{image_config['image_name']}",
    tags=[pulumi.Output.concat(registry_url, "/", image_config["image_name"], ":", timestamp_tag)],
    context=docker_build.BuildContextArgs(location=image_config["context_path"]),
    dockerfile={"location": f"{image_config['context_path']}/{image_config['dockerfile']}"},
    platforms=[docker_build.Platform.LINUX_AMD64],
    push=True,
    cache_from=cache_from,
    cache_to=cache_to,
    opts=pulumi.ResourceOptions(custom_timeouts=CustomTimeouts(create="30m"), retain_on_delete=True, depends_on=[repo]),
)
# Export references to stack
pulumi.export("consult-vector-db-ref", frontend_image.ref)
pulumi.export("consult-vector-db-tags", frontend_image.tags)

# New
# Docker Build + Push -> datapipeline (ML workflow image)
datapipeline_config = {
    "image_name": "consult-app-workflow",  # choose a clear name
    "context_path": "../../datapipeline",  # this directory has your Dockerfile
    "dockerfile": "Dockerfile",
}

cache_from, cache_to = _gha_cache(datapipeline_config["image_name"])
datapipeline_image = docker_build.Image(
    f"build-{datapipeline_config['image_name']}",
    tags=[pulumi.Output.concat(registry_url, "/", datapipeline_config["image_name"], ":", timestamp_tag)],
    context=docker_build.BuildContextArgs(location=datapipeline_config["context_path"]),
    dockerfile={"location": f"{datapipeline_config['context_path']}/{datapipeline_config['dockerfile']}"},
    platforms=[docker_build.Platform.LINUX_AMD64],
    push=True,
    cache_from=cache_from,
    cache_to=cache_to,
    opts=pulumi.ResourceOptions(
        custom_timeouts=CustomTimeouts(create="30m"),
        retain_on_delete=True,
        depends_on=[repo],
    ),
)

pulumi.export("consult-app-workflow-ref", datapipeline_image.ref)
pulumi.export("consult-app-workflow-tags", datapipeline_image.tags)
