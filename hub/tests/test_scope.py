"""웹 RBAC 스코프 강제 단위 테스트."""

import os

os.environ.setdefault("CANGGU_JWT_SECRET", "test-secret")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.core import scope  # noqa: E402
from app.core.auth import Principal  # noqa: E402

CLUSTER = Principal("a", "admin", "cluster", "")           # 전체
CLUSTER_ONE = Principal("b", "viewer", "cluster", "playce")  # 특정 클러스터
NS = Principal("c", "viewer", "namespace", "playce/team-a")  # 특정 NS


def test_cluster_full_all_namespaces():
    assert scope.resolve_scope(CLUSTER, "playce") is None
    assert scope.effective_namespace(CLUSTER, "playce", "team-a") == "team-a"
    assert scope.effective_namespace(CLUSTER, "playce", None) is None


def test_cluster_scoped_to_one_cluster():
    assert scope.resolve_scope(CLUSTER_ONE, "playce") is None
    with pytest.raises(HTTPException):
        scope.resolve_scope(CLUSTER_ONE, "other")


def test_namespace_scope_forces_namespace():
    assert scope.resolve_scope(NS, "playce") == "team-a"
    assert scope.effective_namespace(NS, "playce", None) == "team-a"
    assert scope.effective_namespace(NS, "playce", "all") == "team-a"
    assert scope.effective_namespace(NS, "playce", "team-a") == "team-a"


def test_namespace_scope_denies_other_namespace():
    with pytest.raises(HTTPException):
        scope.effective_namespace(NS, "playce", "team-b")


def test_namespace_scope_denies_other_cluster():
    with pytest.raises(HTTPException):
        scope.resolve_scope(NS, "other")
    assert scope.cluster_allowed(NS, "other") is False
    assert scope.cluster_allowed(NS, "playce") is True
