"""인벤토리 수집기.

Phase 0 는 kube.KubeClient.collect 로 namespace/workload/pod 를 수집한다.
Phase 1+ 에서 metrics / events / health / storage / gateway 수집기가 이 패키지에 추가된다.
"""

from __future__ import annotations

from app.kube import KubeClient


def collect_inventory(kube: KubeClient, cluster_id: str, generation: int) -> dict:
    return kube.collect(cluster_id, generation)
