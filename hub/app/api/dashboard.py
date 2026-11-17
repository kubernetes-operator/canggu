"""대시보드 묶음 API: 웹 새로고침 1회 = 요청 1회.

웹은 새로고침마다 13개 API 를 병렬 호출했는데, Cloudflare 경유 시 요청당 왕복이 0.5~0.9s 라
화면이 다 채워지는 데 오래 걸렸다. 기존 핸들러를 그대로 호출해 응답을 묶으므로 스코프(RBAC)
판정과 응답 형식은 개별 API 와 동일하다. 한 섹션이 실패해도 나머지는 반환(errors 에 기록).
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api import clusters, commands, issues, resources
from app.core import auth, scope

router = APIRouter(prefix="/api/clusters", tags=["dashboard"])


@router.get("/{cluster_id}/dashboard")
async def dashboard(
    request: Request,
    cluster_id: str,
    namespace: str | None = Query(None),
    p: auth.Principal = Depends(auth.require_user),
) -> dict:
    scope.effective_namespace(p, cluster_id, namespace)  # 스코프 밖이면 여기서 403
    sections = {
        "clusters": lambda: clusters.list_clusters(p),
        "namespaces": lambda: resources.namespaces(cluster_id, p),
        "namespace_modes": lambda: clusters.namespace_modes(cluster_id, p),
        "pods": lambda: resources.pods(cluster_id, namespace, p),
        "services": lambda: resources.services(cluster_id, namespace, False, p),
        "workloads": lambda: resources.workloads(cluster_id, namespace, p),
        "storage": lambda: resources.storage(cluster_id, namespace, p),
        "routes": lambda: resources.routes(cluster_id, namespace, p),
        "events": lambda: resources.events(cluster_id, namespace, None, p),
        "operating": lambda: resources.operating(cluster_id, namespace, p),
        "issues": lambda: issues.list_issues(cluster_id, namespace, p),
        "commands": lambda: commands.list_commands(cluster_id, 100),
        "summary": lambda: resources.summary(cluster_id, p),
    }

    async def run(name, fn):
        t0 = time.perf_counter()
        try:
            return name, await fn(), None, time.perf_counter() - t0
        except HTTPException as e:
            return name, None, f"{e.status_code} {e.detail}", time.perf_counter() - t0
        except Exception as e:  # noqa: BLE001 — 섹션 단위 격리
            return name, None, f"500 {type(e).__name__}", time.perf_counter() - t0

    results = await asyncio.gather(*(run(n, f) for n, f in sections.items()))
    out: dict = {"errors": {}}
    for name, value, err, _ in results:
        out[name] = value
        if err:
            out["errors"][name] = err
    # 가장 느린 섹션들을 Server-Timing 에 기록(미들웨어가 app 총 시간과 합쳐 헤더로 보냄).
    slow = sorted(results, key=lambda r: r[3], reverse=True)[:4]
    request.state.server_timing = [(f"s-{n}", d) for n, _, _, d in slow]
    return out
