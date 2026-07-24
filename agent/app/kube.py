"""Kubernetes 접근 계층. 실 클러스터(in-cluster SA / 로컬 kubeconfig) 또는 MOCK.

MOCK 모드는 클러스터/자격증명 없이 합성 인벤토리를 생성하여 전체 파이프라인을 검증한다.
"""

from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger("canggu.agent.kube")


# ── MOCK 인벤토리 ────────────────────────────────────────────────────────────
def _mock_snapshot(cluster_id: str, generation: int) -> dict:
    """의도적으로 team-a/api 워크로드를 저(低)프로비저닝 상태로 만들어 규칙#3 을 트리거."""
    return {
        "cluster_id": cluster_id,
        "generation": generation,
        "namespaces": ["default", "team-a", "team-b"],
        "workloads": [
            {
                "namespace": "team-a",
                "kind": "Deployment",
                "name": "api",
                "replicas_desired": 3,
                "replicas_ready": 3,
                "distinct_nodes": 1,  # 기능8 위반 (Phase 4 에서 조치)
                "containers": [
                    {
                        "name": "api",
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "200m", "memory": "256Mi"},
                        "usage_p95": {"cpu": "450m", "memory": "500Mi"},  # requests 대폭 초과
                    }
                ],
            },
            {
                "namespace": "team-b",
                "kind": "Deployment",
                "name": "worker",
                "replicas_desired": 2,
                "replicas_ready": 2,
                "distinct_nodes": 2,
                "containers": [
                    {
                        "name": "worker",
                        "requests": {"cpu": "500m", "memory": "512Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                        "usage_p95": {"cpu": "300m", "memory": "400Mi"},  # 적정 → 이슈 없음
                    }
                ],
            },
        ],
        "services": [
            {"namespace": "team-a", "name": "api", "type": "ClusterIP",
             "ready_endpoints": 3, "desired_endpoints": 3},
            {"namespace": "team-b", "name": "worker", "type": "ClusterIP",
             "ready_endpoints": 0, "desired_endpoints": 2},  # 비정상 (feature #6 데모)
        ],
        "storage": [
            {"namespace": "team-a", "pvc": "api-data", "pv": "pv-001",
             "storage_class": "nfs-nas-sc-main", "capacity": "5Gi",
             "bound_workloads": ["Deployment/api"], "phase": "Bound"},
            {"namespace": "team-b", "pvc": "worker-cache", "pv": "",
             "storage_class": "nfs-nas-sc-main", "capacity": "",
             "bound_workloads": ["Deployment/worker"], "phase": "Pending"},  # 미바인딩 데모
        ],
        "routes": [
            {"namespace": "team-a", "httproute": "api-route", "gateway": "gateway",
             "hostname": "test2.studiobasa.com", "path": "/api",
             "backend_service": "api", "backend_port": 80, "weight": 1},
            {"namespace": "team-b", "httproute": "worker-route", "gateway": "gateway",
             "hostname": "test2.studiobasa.com", "path": "/worker",
             "backend_service": "worker", "backend_port": 8080, "weight": 1},
        ],
        "velero_installed": True,
        "velero_backups": [
            {"name": "team-a-20260724", "phase": "Completed", "included_namespaces": ["team-a"],
             "created": "2026-07-24T06:00:00Z", "completed": "2026-07-24T06:02:00Z",
             "errors": 0, "warnings": 0},
        ],
        "velero_restores": [],
        "velero_schedules": [
            {"name": "team-a-daily", "cron": "0 2 * * *", "included_namespaces": ["team-a"],
             "paused": False, "last_backup": "2026-07-24T02:00:00Z"},
        ],
        "pods": [
            {"namespace": "team-a", "name": "api-1", "node": "node-1", "phase": "Running",
             "restart_count": 0, "owner_kind": "Deployment", "owner_name": "api"},
            {"namespace": "team-a", "name": "api-2", "node": "node-1", "phase": "Running",
             "restart_count": 3, "waiting_reason": "CrashLoopBackOff",
             "owner_kind": "Deployment", "owner_name": "api"},
            {"namespace": "team-a", "name": "api-3", "node": "node-1", "phase": "Running",
             "restart_count": 0, "owner_kind": "Deployment", "owner_name": "api"},
            {"namespace": "team-b", "name": "worker-1", "node": "node-1", "phase": "Running",
             "restart_count": 0, "owner_kind": "Deployment", "owner_name": "worker"},
            {"namespace": "team-b", "name": "worker-2", "node": "node-2", "phase": "Running",
             "restart_count": 0, "owner_kind": "Deployment", "owner_name": "worker"},
        ],
    }


# ── 실 클러스터 클라이언트 ────────────────────────────────────────────────────
class KubeClient:
    def __init__(self) -> None:
        self._apps = None
        self._core = None
        self._custom = None
        self._metrics_available = False
        self._prom_cache: dict = {}
        self._prom_cache_ts = -1e9
        s = get_settings()
        self.mock = s.mock
        if self.mock:
            self._metrics_available = True  # mock 은 합성 usage 제공
        else:
            self._init_real(s.use_local_kubeconfig)

    def _init_real(self, use_local: bool) -> None:
        from kubernetes import client, config  # lazy import

        try:
            if use_local:
                config.load_kube_config()
            else:
                config.load_incluster_config()
        except Exception as e:  # noqa: BLE001
            log.error("kube config 로드 실패: %s (MOCK=1 로 실행하거나 kubeconfig 확인)", e)
            raise
        self._apps = client.AppsV1Api()
        self._core = client.CoreV1Api()
        self._custom = client.CustomObjectsApi()  # metrics.k8s.io
        self._rbac = client.RbacAuthorizationV1Api()

    @property
    def metrics_available(self) -> bool:
        return self._metrics_available

    def collect(self, cluster_id: str, generation: int) -> dict:
        if self.mock:
            return _mock_snapshot(cluster_id, generation)
        return self._collect_real(cluster_id, generation)

    def _collect_real(self, cluster_id: str, generation: int) -> dict:
        """실 인벤토리 + metrics-server 사용량 + Service 헬스."""
        core, apps = self._core, self._apps
        namespaces = [ns.metadata.name for ns in core.list_namespace().items]

        # 1) pods + owner 매핑 (+ PVC 사용 워크로드 매핑)
        pods = []
        nodes_by_owner: dict[tuple[str, str], set[str]] = {}
        pods_by_owner: dict[tuple[str, str], list[str]] = {}
        pvc_users: dict[tuple[str, str], set[str]] = {}
        for p in core.list_pod_for_all_namespaces().items:
            owner_kind, owner_name = _owner(p)
            node = p.spec.node_name or ""
            for vol in (p.spec.volumes or []):
                if vol.persistent_volume_claim and owner_name:
                    pvc_users.setdefault(
                        (p.metadata.namespace, vol.persistent_volume_claim.claim_name), set()
                    ).add(f"{owner_kind}/{owner_name}")
            restart = sum((cs.restart_count or 0) for cs in (p.status.container_statuses or []))
            pods.append({
                "namespace": p.metadata.namespace,
                "name": p.metadata.name,
                "node": node,
                "phase": p.status.phase or "",
                "restart_count": restart,
                "waiting_reason": _first_waiting(p),
                "last_terminated_reason": _first_terminated(p),
                "owner_kind": owner_kind,
                "owner_name": owner_name,
            })
            if owner_name:
                if node:
                    nodes_by_owner.setdefault((p.metadata.namespace, owner_name), set()).add(node)
                pods_by_owner.setdefault((p.metadata.namespace, owner_name), []).append(
                    p.metadata.name
                )

        # 2) 사용량 p95: Prometheus 우선(설정 시), 실패/미설정 시 metrics-server 순간값 폴백.
        metrics = None
        if get_settings().prometheus_url:
            metrics = self._prometheus_metrics_map()
        if metrics is None:
            metrics = self._pod_metrics_map()
        else:
            self._metrics_available = True

        # 3) workloads (+ 컨테이너별 워크로드 내 최대 사용량 = per-replica 우측정렬 기준)
        workloads = []
        for d in apps.list_deployment_for_all_namespaces().items:
            ns, name = d.metadata.namespace, d.metadata.name
            owned_pods = pods_by_owner.get((ns, name), [])
            containers = []
            for c in (d.spec.template.spec.containers or []):
                cpu_max, mem_max = _max_usage(metrics, ns, owned_pods, c.name)
                containers.append({
                    "name": c.name,
                    "requests": _res(c.resources.requests if c.resources else None),
                    "limits": _res(c.resources.limits if c.resources else None),
                    "usage_p95": {"cpu": cpu_max, "memory": mem_max},  # Phase1: 순간최대(히스토리 p95는 Phase2)
                })
            pod_spec = d.spec.template.spec
            has_spread = bool(pod_spec.topology_spread_constraints) or bool(
                pod_spec.affinity and pod_spec.affinity.pod_anti_affinity
            )
            workloads.append({
                "namespace": ns,
                "kind": "Deployment",
                "name": name,
                "replicas_desired": d.spec.replicas or 0,
                "replicas_ready": d.status.ready_replicas or 0,
                "distinct_nodes": len(nodes_by_owner.get((ns, name), set())),
                "has_spread_constraints": has_spread,
                "containers": containers,
            })

        # 4) services + endpoints 헬스
        services = self._collect_services()

        # 5) 스토리지 링크(SC↔PV↔PVC↔워크로드) + 6) Gateway HTTPRoute 흐름 + 7) Velero
        storage = self._collect_storage(pvc_users)
        routes = self._collect_routes()
        velero_installed, velero_backups, velero_restores, velero_schedules = self._collect_velero()

        return {
            "cluster_id": cluster_id,
            "generation": generation,
            "namespaces": namespaces,
            "workloads": workloads,
            "pods": pods,
            "services": services,
            "storage": storage,
            "routes": routes,
            "velero_installed": velero_installed,
            "velero_backups": velero_backups,
            "velero_restores": velero_restores,
            "velero_schedules": velero_schedules,
        }

    def _collect_storage(self, pvc_users: dict) -> list[dict]:
        core = self._core
        out = []
        for pvc in core.list_persistent_volume_claim_for_all_namespaces().items:
            ns, name = pvc.metadata.namespace, pvc.metadata.name
            cap = ""
            if pvc.status and pvc.status.capacity:
                cap = str(pvc.status.capacity.get("storage", ""))
            out.append({
                "namespace": ns,
                "pvc": name,
                "pv": pvc.spec.volume_name or "",
                "storage_class": pvc.spec.storage_class_name or "",
                "capacity": cap,
                "bound_workloads": sorted(pvc_users.get((ns, name), set())),
                "phase": (pvc.status.phase if pvc.status else "") or "",
            })
        return out

    def _collect_velero(self) -> tuple[bool, list[dict], list[dict], list[dict]]:
        """Velero 백업/복구/스케줄 수집. 미설치면 (False, [], [], [])."""
        vns = get_settings().velero_namespace
        try:
            bk = self._custom.list_namespaced_custom_object("velero.io", "v1", vns, "backups")
        except Exception:  # noqa: BLE001 — CRD 미설치/네임스페이스 없음
            return (False, [], [], [])
        backups = []
        for it in bk.get("items", []):
            st = it.get("status", {}) or {}
            backups.append({
                "name": it["metadata"]["name"],
                "phase": st.get("phase", ""),
                "included_namespaces": (it.get("spec", {}) or {}).get("includedNamespaces", []) or [],
                "created": (it.get("metadata", {}) or {}).get("creationTimestamp", ""),
                "completed": st.get("completionTimestamp", "") or "",
                "errors": int(st.get("errors", 0) or 0),
                "warnings": int(st.get("warnings", 0) or 0),
            })
        restores = []
        try:
            rs = self._custom.list_namespaced_custom_object("velero.io", "v1", vns, "restores")
            for it in rs.get("items", []):
                st = it.get("status", {}) or {}
                restores.append({
                    "name": it["metadata"]["name"],
                    "backup_name": (it.get("spec", {}) or {}).get("backupName", ""),
                    "phase": st.get("phase", ""),
                    "created": (it.get("metadata", {}) or {}).get("creationTimestamp", ""),
                    "errors": int(st.get("errors", 0) or 0),
                    "warnings": int(st.get("warnings", 0) or 0),
                })
        except Exception:  # noqa: BLE001
            pass
        schedules = []
        try:
            sc = self._custom.list_namespaced_custom_object("velero.io", "v1", vns, "schedules")
            for it in sc.get("items", []):
                spec = it.get("spec", {}) or {}
                st = it.get("status", {}) or {}
                tmpl = spec.get("template", {}) or {}
                schedules.append({
                    "name": it["metadata"]["name"],
                    "cron": spec.get("schedule", ""),
                    "included_namespaces": tmpl.get("includedNamespaces", []) or [],
                    "paused": bool(spec.get("paused", False)),
                    "last_backup": st.get("lastBackup", "") or "",
                })
        except Exception:  # noqa: BLE001
            pass
        return (True, backups, restores, schedules)

    def create_velero_schedule(self, namespace: str, name: str, cron: str) -> str:
        vns = get_settings().velero_namespace
        if self.mock:
            return name
        body = {
            "apiVersion": "velero.io/v1", "kind": "Schedule",
            "metadata": {"name": name, "namespace": vns},
            "spec": {"schedule": cron,
                     "template": {"includedNamespaces": [namespace], "storageLocation": "default"}},
        }
        self._custom.create_namespaced_custom_object("velero.io", "v1", vns, "schedules", body)
        return name

    def delete_velero_schedule(self, name: str) -> str:
        vns = get_settings().velero_namespace
        if self.mock:
            return name
        self._custom.delete_namespaced_custom_object("velero.io", "v1", vns, "schedules", name)
        return name

    def create_velero_backup(self, namespace: str, name: str) -> str:
        """네임스페이스 백업 생성(Backup CR). 반환: 생성된 이름."""
        vns = get_settings().velero_namespace
        if self.mock:
            return name
        body = {
            "apiVersion": "velero.io/v1", "kind": "Backup",
            "metadata": {"name": name, "namespace": vns},
            "spec": {"includedNamespaces": [namespace], "storageLocation": "default"},
        }
        self._custom.create_namespaced_custom_object("velero.io", "v1", vns, "backups", body)
        return name

    def create_velero_restore(self, backup_name: str, name: str) -> str:
        vns = get_settings().velero_namespace
        if self.mock:
            return name
        body = {
            "apiVersion": "velero.io/v1", "kind": "Restore",
            "metadata": {"name": name, "namespace": vns},
            "spec": {"backupName": backup_name},
        }
        self._custom.create_namespaced_custom_object("velero.io", "v1", vns, "restores", body)
        return name

    def _collect_routes(self) -> list[dict]:
        """Gateway API HTTPRoute 흐름. CRD 미설치면 빈 리스트(fail-safe)."""
        try:
            data = self._custom.list_cluster_custom_object(
                "gateway.networking.k8s.io", "v1", "httproutes"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("httproutes 조회 실패(흐름 생략): %s", e)
            return []
        edges = []
        for item in data.get("items", []):
            md = item.get("metadata", {})
            spec = item.get("spec", {})
            ns, name = md.get("namespace", ""), md.get("name", "")
            hosts = spec.get("hostnames", []) or [""]
            parents = spec.get("parentRefs", []) or [{}]
            gw = parents[0].get("name", "") if parents else ""
            hostname = hosts[0]
            for rule in spec.get("rules", []) or []:
                paths = [m.get("path", {}).get("value", "") for m in (rule.get("matches") or [{}])]
                path = paths[0] if paths else ""
                for be in rule.get("backendRefs", []) or []:
                    edges.append({
                        "namespace": ns, "httproute": name, "gateway": gw,
                        "hostname": hostname, "path": path,
                        "backend_service": be.get("name", ""),
                        "backend_port": int(be.get("port", 0) or 0),
                        "weight": int(be.get("weight", 1) or 1),
                    })
        return edges

    def _pod_metrics_map(self) -> dict:
        """(ns, pod) -> {container: (cpu, memory)}. metrics-server 없으면 빈 dict."""
        try:
            data = self._custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
        except Exception as e:  # noqa: BLE001
            log.warning("metrics-server 조회 실패(메트릭 없이 진행): %s", e)
            self._metrics_available = False
            return {}
        self._metrics_available = True
        out: dict = {}
        for item in data.get("items", []):
            md = item.get("metadata", {})
            cm = {c["name"]: (c.get("usage", {}).get("cpu", ""),
                              c.get("usage", {}).get("memory", ""))
                  for c in item.get("containers", [])}
            out[(md.get("namespace"), md.get("name"))] = cm
        return out

    def _prometheus_metrics_map(self) -> dict | None:
        """Prometheus 에서 (ns,pod)->{container:(cpu,mem)} p95 조회. 60s 캐시. 실패 시 None."""
        import json
        import time
        import urllib.parse
        import urllib.request

        s = get_settings()
        now = time.monotonic()
        if self._prom_cache and now - self._prom_cache_ts < s.prometheus_cache_seconds:
            return self._prom_cache

        win, q = s.prometheus_window, s.prometheus_quantile
        base = 'container!="",container!="POD"'
        q_cpu = (f'quantile_over_time({q}, sum by (namespace,pod,container) '
                 f'(rate(container_cpu_usage_seconds_total{{{base}}}[5m]))[{win}:5m])')
        q_mem = (f'quantile_over_time({q}, sum by (namespace,pod,container) '
                 f'(container_memory_working_set_bytes{{{base}}})[{win}:5m])')

        def query(expr: str) -> dict:
            url = s.prometheus_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode(
                {"query": expr}
            )
            with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310
                return json.load(r)

        try:
            cpu, mem = query(q_cpu), query(q_mem)
        except Exception as e:  # noqa: BLE001
            log.warning("prometheus 조회 실패(metrics-server 폴백): %s", e)
            return None

        out: dict = {}
        for res in cpu.get("data", {}).get("result", []):
            m = res["metric"]
            cores = float(res["value"][1])
            out.setdefault((m.get("namespace"), m.get("pod")), {})[m.get("container")] = [
                f"{max(1, round(cores * 1000))}m", ""
            ]
        for res in mem.get("data", {}).get("result", []):
            m = res["metric"]
            mib = max(1, round(float(res["value"][1]) / 1024 / 1024))
            cell = out.setdefault((m.get("namespace"), m.get("pod")), {}).setdefault(
                m.get("container"), ["", ""]
            )
            cell[1] = f"{mib}Mi"
        result = {k: {c: (v[0], v[1]) for c, v in cm.items()} for k, cm in out.items()}
        self._prom_cache, self._prom_cache_ts = result, now
        log.info("prometheus p95 수집: %d pod", len(result))
        return result

    def _collect_services(self) -> list[dict]:
        core = self._core
        eps: dict[tuple[str, str], tuple[int, int]] = {}
        for ep in core.list_endpoints_for_all_namespaces().items:
            ready = sum(len(s.addresses or []) for s in (ep.subsets or []))
            notready = sum(len(s.not_ready_addresses or []) for s in (ep.subsets or []))
            eps[(ep.metadata.namespace, ep.metadata.name)] = (ready, notready)
        services = []
        for svc in core.list_service_for_all_namespaces().items:
            if (svc.spec.type or "") == "ExternalName":
                continue
            ready, notready = eps.get((svc.metadata.namespace, svc.metadata.name), (0, 0))
            services.append({
                "namespace": svc.metadata.namespace,
                "name": svc.metadata.name,
                "type": svc.spec.type or "",
                "ready_endpoints": ready,
                "desired_endpoints": ready + notready,
            })
        return services

    def apply_patch(
        self, namespace: str, kind: str, name: str, patch: dict, dry_run: bool
    ) -> tuple[str, str]:
        """패치 적용. (rv_before, rv_after) 반환. MOCK 은 시뮬레이션."""
        if self.mock:
            log.info("[MOCK] patch %s/%s in %s dry_run=%s -> %s",
                     kind, name, namespace, dry_run, patch)
            return ("mock-1", "mock-1" if dry_run else "mock-2")

        from kubernetes import client  # lazy

        apps = self._apps
        before = _read_rv(apps, kind, name, namespace)
        opts = ["All"] if dry_run else []
        try:
            if kind == "Deployment":
                resp = apps.patch_namespaced_deployment(
                    name, namespace, patch, dry_run=",".join(opts) or None
                )
            elif kind == "StatefulSet":
                resp = apps.patch_namespaced_stateful_set(
                    name, namespace, patch, dry_run=",".join(opts) or None
                )
            else:
                raise ValueError(f"지원하지 않는 kind: {kind}")
        except client.ApiException as e:
            raise RuntimeError(f"patch 실패: {e.status} {e.reason}") from e
        after = resp.metadata.resource_version if not dry_run else before
        return (before, after)


    def inject_topology_spread(
        self, namespace: str, kind: str, name: str, params: dict, dry_run: bool
    ) -> tuple[str, str, str]:
        """소유 컨트롤러 PodTemplate 에 topologySpreadConstraints 주입.

        기존 topologySpreadConstraints/podAntiAffinity 가 있으면 덮어쓰지 않고 skip.
        반환: (rv_before, rv_after, skipped_reason)  skipped_reason 이 있으면 미적용.
        """
        if self.mock:
            return ("mock-1", "mock-1" if dry_run else "mock-2", "")

        from kubernetes import client  # lazy

        apps = self._apps
        reader = {
            "Deployment": apps.read_namespaced_deployment,
            "StatefulSet": apps.read_namespaced_stateful_set,
        }.get(kind)
        if reader is None:
            raise ValueError(f"지원하지 않는 kind: {kind}")
        obj = reader(name, namespace)
        pod_spec = obj.spec.template.spec
        if pod_spec.topology_spread_constraints or (
            pod_spec.affinity and pod_spec.affinity.pod_anti_affinity
        ):
            return ("", "", "이미 분산 제약(topologySpread/podAntiAffinity) 존재 — 덮어쓰지 않음")

        selector = (obj.spec.selector.match_labels or {}) if obj.spec.selector else {}
        before = obj.metadata.resource_version
        constraint = {
            "maxSkew": int(params.get("maxSkew", 1)),
            "topologyKey": params.get("topologyKey", "kubernetes.io/hostname"),
            "whenUnsatisfiable": params.get("whenUnsatisfiable", "ScheduleAnyway"),
            "labelSelector": {"matchLabels": selector},
        }
        patch = {"spec": {"template": {"spec": {"topologySpreadConstraints": [constraint]}}}}
        opts = "All" if dry_run else None
        try:
            if kind == "Deployment":
                resp = apps.patch_namespaced_deployment(name, namespace, patch, dry_run=opts)
            else:
                resp = apps.patch_namespaced_stateful_set(name, namespace, patch, dry_run=opts)
        except client.ApiException as e:
            raise RuntimeError(f"spread 주입 실패: {e.status} {e.reason}") from e
        after = resp.metadata.resource_version if not dry_run else before
        return (before, after, "")

    def issue_namespace_kubeconfig(
        self, namespace: str, role: str, sa_name: str, ttl: int, server: str, context_name: str
    ) -> str:
        """NS 에 SA + RoleBinding(내장 ClusterRole) 생성 후 TokenRequest 단기토큰으로 kubeconfig 생성.

        장기 Secret 토큰이 아닌 bound token(만료 있음)을 발급한다. 토큰은 저장하지 않고 반환만.
        """
        role_map = {"admin": "admin", "edit": "edit", "view": "view"}
        crole = role_map.get(role)
        if not crole:
            raise ValueError(f"지원하지 않는 role: {role} (admin|edit|view)")

        if self.mock:
            return (
                f"# MOCK kubeconfig — sa={sa_name} ns={namespace} role={crole}\n"
                "apiVersion: v1\nkind: Config\ncurrent-context: mock\n"
                "clusters: [{name: mock, cluster: {server: https://mock:6443}}]\n"
                f"users: [{{name: {sa_name}, user: {{token: MOCK-TOKEN}}}}]\n"
                f"contexts: [{{name: mock, context: {{cluster: mock, namespace: {namespace}, user: {sa_name}}}}}]\n"
            )

        import base64
        import os

        import yaml
        from kubernetes import client

        core, rbac = self._core, self._rbac
        meta = client.V1ObjectMeta(name=sa_name, namespace=namespace)
        try:
            core.create_namespaced_service_account(namespace, client.V1ServiceAccount(metadata=meta))
        except client.ApiException as e:
            if e.status != 409:
                raise

        rb_name = f"canggu-{sa_name}-{crole}"
        rb = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name=rb_name, namespace=namespace),
            role_ref=client.V1RoleRef(
                api_group="rbac.authorization.k8s.io", kind="ClusterRole", name=crole
            ),
            subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=namespace)],
        )
        try:
            rbac.create_namespaced_role_binding(namespace, rb)
        except client.ApiException as e:
            if e.status != 409:
                raise

        tr = client.AuthenticationV1TokenRequest(
            spec=client.V1TokenRequestSpec(expiration_seconds=int(ttl))
        )
        resp = core.create_namespaced_service_account_token(sa_name, namespace, tr)
        token = resp.status.token

        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        cluster_entry: dict = {"server": server or _incluster_server()}
        if os.path.exists(ca_path):
            with open(ca_path, "rb") as f:
                cluster_entry["certificate-authority-data"] = base64.b64encode(f.read()).decode()
        else:
            cluster_entry["insecure-skip-tls-verify"] = True

        ctx = f"{namespace}-{crole}"
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": context_name, "cluster": cluster_entry}],
            "users": [{"name": sa_name, "user": {"token": token}}],
            "contexts": [
                {"name": ctx, "context": {"cluster": context_name, "namespace": namespace,
                                          "user": sa_name}}
            ],
            "current-context": ctx,
        }
        return yaml.safe_dump(kubeconfig, sort_keys=False)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _incluster_server() -> str:
    import os

    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    return f"https://{host}:{port}"


def _cpu_to_nano(q: str) -> int:
    """metrics-server cpu quantity -> nanocores(int) 비교용."""
    q = (q or "").strip()
    if not q:
        return -1
    try:
        if q.endswith("n"):
            return int(float(q[:-1]))
        if q.endswith("u"):
            return int(float(q[:-1]) * 1_000)
        if q.endswith("m"):
            return int(float(q[:-1]) * 1_000_000)
        return int(float(q) * 1_000_000_000)
    except ValueError:
        return -1


def _mem_to_bytes(q: str) -> int:
    q = (q or "").strip()
    if not q:
        return -1
    suf = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
           "K": 1000, "M": 1000**2, "G": 1000**3}
    try:
        for s, mul in suf.items():
            if q.endswith(s):
                return int(float(q[: -len(s)]) * mul)
        return int(float(q))
    except ValueError:
        return -1


def _max_usage(metrics: dict, ns: str, pod_names: list[str], container: str) -> tuple[str, str]:
    """워크로드 소유 pod 들 중 해당 컨테이너의 최대 cpu/memory 사용량(원본 quantity 문자열)."""
    best_cpu_n, best_cpu = -1, ""
    best_mem_b, best_mem = -1, ""
    for pn in pod_names:
        cm = metrics.get((ns, pn), {})
        u = cm.get(container)
        if not u:
            continue
        cpu_s, mem_s = u
        if (cn := _cpu_to_nano(cpu_s)) > best_cpu_n:
            best_cpu_n, best_cpu = cn, cpu_s
        if (mb := _mem_to_bytes(mem_s)) > best_mem_b:
            best_mem_b, best_mem = mb, mem_s
    return best_cpu, best_mem


def _res(d) -> dict:
    d = d or {}
    return {"cpu": str(d.get("cpu", "")), "memory": str(d.get("memory", ""))}


def _owner(p) -> tuple[str, str]:
    refs = p.metadata.owner_references or []
    if not refs:
        return "", ""
    ref = refs[0]
    # ReplicaSet -> Deployment 이름 근사 (해시 접미사 제거)
    if ref.kind == "ReplicaSet" and "-" in ref.name:
        return "Deployment", ref.name.rsplit("-", 1)[0]
    return ref.kind, ref.name


def _first_waiting(p) -> str:
    for cs in (p.status.container_statuses or []):
        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            return cs.state.waiting.reason
    return ""


def _first_terminated(p) -> str:
    for cs in (p.status.container_statuses or []):
        last = cs.last_state.terminated if cs.last_state else None
        if last and last.reason:
            return last.reason
    return ""


def _read_rv(apps, kind: str, name: str, namespace: str) -> str:
    try:
        if kind == "Deployment":
            return apps.read_namespaced_deployment(name, namespace).metadata.resource_version
        if kind == "StatefulSet":
            return apps.read_namespaced_stateful_set(name, namespace).metadata.resource_version
    except Exception:  # noqa: BLE001
        return ""
    return ""
