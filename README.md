# canggu

**Kubernetes 자동 운영 플랫폼** — 리소스 이슈를 탐지하고, 해결안을 제시하며, 승인 또는 자동으로 수정한다.

멀티클러스터를 hub-spoke 구조로 운영한다. 각 클러스터에 **agent**를 배포하고 중앙 **hub**가 텔레메트리를 집계·분석하며, 운영자는 권한(전체 클러스터 / 네임스페이스, admin / viewer)에 따라 제한된 웹 UI로 확인·조작한다. 자동 조정은 언제든 **관찰 전용(observe)** 으로 멈출 수 있고, 모든 변경은 감사된다.

> 웹: `https://<gateway-host>/operating/` · 상세 문서: [architecture](docs/architecture.md) · [remediation-rules](docs/remediation-rules.md) · [rbac-model](docs/rbac-model.md) · [operations](docs/operations.md)

---

## 기능

| # | 기능 | 설명 |
|---|------|------|
| 1 | HTTPRoute 트래픽 흐름 | Gateway API HTTPRoute별 `hostname → gateway → route → backend service` 흐름 시각화 |
| 2 | 성능치 표시 | Prometheus p95(폴백 metrics-server) 사용량 vs requests/limits |
| 3 | Namespace 구분 | 전 뷰 namespace 필터 + 웹 RBAC 스코프 |
| 4 | Multicluster | hub-spoke agent 모델(클러스터별 등록) |
| 5 | 재기동 Pod | restartCount 실시간 표시 |
| 6 | Service 헬스 | endpoints ready/desired 기반 정상 여부 |
| 7 | 스토리지 토폴로지 | SC ↔ PV ↔ PVC ↔ 사용 워크로드 연결 |
| 8 | 노드 분산 강제 | 파드가 2노드 미만인 워크로드에 topologySpreadConstraints 자동 주입 |
| 9 | kubeconfig 발급 | 네임스페이스별 SA(admin/edit/view) + TokenRequest 단기토큰 다운로드 |
| 10 | Velero 백업/복구 | 네임스페이스별 백업·복구·정기 스케줄 시각화·실행 |

**자동 조정 엔진** — `Detector → Issue → 해결안(patch) → 모드 게이트 → Command → 결과/감사`
- 규칙 7종: 리소스 우측정렬 · CPU throttling · OOMKilled · CrashLoop(진단) · 노드 분산 · unschedulable(진단) · ImagePullBackOff(진단)
- 클러스터/네임스페이스별 **observe(제안만) / active(자동 적용)** 모드 (유효 모드 = min(cluster, ns))
- 전역 freeze kill-switch, 규칙별 활성/auto_apply 런타임 설정, 동일 이슈 쿨다운(안티플래핑), 적용 전 dry-run admission 검증
- 수동 리소스 편집(dry-run 지원), 자동 조정 라이브 피드, 클러스터 헬스 요약 대시보드

**보안 / 접근 제어**
- 로그인 인증(서명 세션 토큰), 본인 비밀번호 변경
- 웹 RBAC 2단계: `admin`(변경) / `viewer`(읽기) × `전체 클러스터` / `특정 네임스페이스` 스코프 — 서버 측 강제
- 웹 사용자 관리(생성/삭제/비번·권한 변경), 라이브 피드 WS 인증 + 스코프 필터
- 발급 kubeconfig는 단기 bound token만 사용, DB엔 메타데이터만 저장

---

## 아키텍처

```
┌────────────┐   gRPC bidi stream (현재 WS 폴백)      ┌──────────────────────┐
│  agent     │ ── telemetry push / command receive ─▶ │  hub (FastAPI)       │
│ (클러스터당)│ ◀─ Command{patch, dry_run, ttl, sig} ─ │  + SQLite/Postgres   │
└────────────┘                                          └──────────┬───────────┘
  in-cluster SA 로 조회/변경                                        │ REST + WS(SPA)
  collectors / actuators                                 ┌────────▼─────────┐
                                                          │  web (React+TS)  │
                                                          └──────────────────┘
```

| 컴포넌트 | 위치 | 설명 |
|---------|------|------|
| **hub** | `hub/` | FastAPI. 클러스터 레지스트리, 텔레메트리 집계, 자동조정 엔진, 인증/웹 RBAC, 감사, 웹 SPA 서빙 |
| **agent** | `agent/` | 클러스터당 배포. 수집기(collectors) + 액추에이터(actuators). hub로 outbound 접속 |
| **web** | `web/` | React + TypeScript + Vite (hub 이미지에 번들되어 `/operating/` 에서 서빙) |
| **proto** | `proto/` | agent↔hub gRPC 계약(정식). 현재 WebSocket 폴백 전송 사용 |
| **deploy** | `deploy/` | k8s 매니페스트(`k8s/`, kustomize) · ArgoCD Application(`argocd/`) · Helm 차트 · agent RBAC |

기술 스택: Python 3.12 · FastAPI · SQLAlchemy · `kubernetes` client · React 18 · TypeScript · Vite. 메트릭은 Prometheus(kube-prometheus-stack) / metrics-server, Gateway는 Gateway API 표준.

---

## 로컬 개발 (클러스터 불필요)

```bash
docker compose up --build      # postgres + redis + hub + (합성데이터) agent-mock
cd web && npm install && npm run dev   # http://localhost:5173
```

개별 실행: `make hub` · `make agent-mock` · `make web` · 테스트: `make test` (hub `pytest`, agent `pytest`).
`CANGGU_MOCK=1` agent는 실제 클러스터/자격증명 없이 합성 데이터로 전체 파이프라인을 구동한다.

---

## 배포 (GitOps / ArgoCD)

이미지는 `registry.local.cloud:5000` 에 배포하고, ArgoCD가 `deploy/k8s`(kustomize)를 추적한다.

```bash
# 1) 이미지 빌드/푸시
make images push

# 2) 시크릿 부트스트랩 (git 밖, 1회)
kubectl create namespace canggu-system
kubectl -n canggu-system create secret generic canggu-secrets \
  --from-literal=agentToken="$(openssl rand -hex 24)" \
  --from-literal=jwtSecret="$(openssl rand -hex 32)" \
  --from-literal=adminPassword="$(openssl rand -base64 18)"

# 3) ArgoCD Application 등록 (최초 1회)
kubectl apply -f deploy/argocd/application.yaml
```

이후 **버전 업**은 `deploy/k8s/kustomization.yaml` 의 `images.newTag` 를 수정하고 커밋/푸시하면 ArgoCD가 자동 배포한다(prune + selfHeal). 직접 `kubectl apply`/태그 수정은 selfHeal로 되돌려지므로 반드시 git을 통해 변경한다. ArgoCD 없이 직접 배포하려면 `make deploy`(`kubectl apply -k deploy/k8s`).

로그인 계정 / kubeconfig 발급 / Velero / 규칙 설정 등 운영 절차는 [docs/operations.md](docs/operations.md).

---

## 개발 원칙 (보안)

- **실제 클러스터 자격증명·시크릿을 코드베이스에 커밋하지 않는다.** dev 기본값만 코드에 있고, 운영 값은 k8s Secret으로 주입.
- 자동 조정은 클러스터/네임스페이스별 observe 모드 및 전역 freeze로 언제든 개입 중단 가능.
- 리소스 변경은 적용 전 dry-run admission 검증, 동일 이슈 쿨다운으로 blast-radius 억제.

---

## 상태

요구 기능 10/10 + 자동조정 엔진 + 인증·per-namespace RBAC + Prometheus p95 + 헬스 대시보드 + 운영 안전장치(preflight · liveness · stale) 구현 완료. 테스트: hub 27 / agent 16. 로드맵·상태 상세는 [docs/architecture.md](docs/architecture.md).
