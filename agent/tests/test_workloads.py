"""컨트롤러 → workload 텔레메트리 변환 (Deployment/StatefulSet/DaemonSet)."""

from kubernetes.client import (
    V1Container, V1DaemonSet, V1DaemonSetSpec, V1DaemonSetStatus, V1LabelSelector,
    V1ObjectMeta, V1PodSpec, V1PodTemplateSpec, V1ResourceRequirements, V1StatefulSet,
    V1StatefulSetSpec, V1StatefulSetStatus,
)

from app.kube import _workload_entry


def _tpl():
    return V1PodTemplateSpec(spec=V1PodSpec(containers=[V1Container(
        name="db", resources=V1ResourceRequirements(requests={"cpu": "100m", "memory": "128Mi"}),
    )]))


def test_statefulset_entry():
    sts = V1StatefulSet(
        metadata=V1ObjectMeta(namespace="a", name="db"),
        spec=V1StatefulSetSpec(replicas=3, selector=V1LabelSelector(), service_name="db",
                               template=_tpl()),
        status=V1StatefulSetStatus(replicas=3, ready_replicas=2),
    )
    metrics = {("a", "db-0"): {"db": ("200m", "100Mi")}}
    w = _workload_entry("StatefulSet", sts, metrics, ["db-0", "db-1"], 2)
    assert (w["kind"], w["replicas_desired"], w["replicas_ready"], w["distinct_nodes"]) == \
        ("StatefulSet", 3, 2, 2)
    assert w["containers"][0]["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert w["containers"][0]["usage_p95"] == {"cpu": "200m", "memory": "100Mi"}


def test_daemonset_entry_uses_scheduled_counts():
    ds = V1DaemonSet(
        metadata=V1ObjectMeta(namespace="kube-system", name="agent"),
        spec=V1DaemonSetSpec(selector=V1LabelSelector(), template=_tpl()),
        status=V1DaemonSetStatus(current_number_scheduled=8, desired_number_scheduled=8,
                                 number_misscheduled=0, number_ready=7),
    )
    w = _workload_entry("DaemonSet", ds, {}, [], 8)
    assert (w["replicas_desired"], w["replicas_ready"]) == (8, 7)
    assert w["has_spread_constraints"] is False
