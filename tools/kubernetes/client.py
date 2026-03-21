"""
Kubernetes client factory.
Supports both in-cluster (production) and kubeconfig (local dev) authentication.
"""

from __future__ import annotations

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
        logger.info("Loading kubeconfig", path=settings.k8s_kubeconfig_path)
        config.load_kube_config(config_file=settings.k8s_kubeconfig_path)
    return ApiClient()


def get_core_v1() -> client.CoreV1Api:
    return client.CoreV1Api(api_client=get_k8s_client())


def get_apps_v1() -> client.AppsV1Api:
    return client.AppsV1Api(api_client=get_k8s_client())
