"""
Kubernetes client factory.
Supports both in-cluster (production) and kubeconfig (local dev) authentication.
"""

from __future__ import annotations

import os
from functools import lru_cache

from kubernetes import client, config
from kubernetes.client import ApiClient

from configs.settings import get_settings
from observability.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_k8s_client() -> ApiClient:
    settings = get_settings()
    if settings.k8s_in_cluster:
        logger.info("Loading in-cluster Kubernetes config")
        config.load_incluster_config()
    else:
        kubeconfig_path = os.path.expanduser(settings.k8s_kubeconfig_path)
        logger.info("Loading kubeconfig", path=kubeconfig_path)
        config.load_kube_config(config_file=kubeconfig_path)

    # Allow overriding the API server host — needed when the agent runs inside
    # Docker and the kubeconfig has 127.0.0.1 (Kind on host, not reachable as localhost)
    k8s_server_override = os.environ.get("K8S_SERVER_OVERRIDE")
    if k8s_server_override:
        configuration = client.configuration.Configuration.get_default_copy()
        configuration.host = k8s_server_override
        configuration.verify_ssl = False
        client.Configuration.set_default(configuration)
        logger.info("K8s server overridden", host=k8s_server_override)

    return ApiClient()


def get_core_v1() -> client.CoreV1Api:
    return client.CoreV1Api(api_client=get_k8s_client())


def get_apps_v1() -> client.AppsV1Api:
    return client.AppsV1Api(api_client=get_k8s_client())
