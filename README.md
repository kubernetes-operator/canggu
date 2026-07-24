# canggu

Kubernetes 자동 운영 플랫폼 — 이슈 탐지 · 해결안 제시 · (승인/자동) 수정.

멀티클러스터를 hub-spoke 구조로 운영한다. 각 클러스터에 **agent**를 배포하고 중앙 **hub**가 텔레메트리를 집계하며, 웹 UI에서 권한(전체 클러스터 / 네임스페이스, view/admin)에 따라 확인·조작한다.

> 상세 설계는 [`docs/architecture.md`](docs/architecture.md), 자동조정 규칙은 [`docs/remediation-rules.md`](docs/remediation-rules.md), 권한 모델은 [`docs/rbac-model.md`](docs/rbac-model.md) 참고.

## 구성 요소

| 컴포넌트 | 위치 | 설명 |
|---------|------|------|
| **hub** | `hub/` | 중앙 FastAPI. 클러스터 레지스트리, 텔레메트리 집계, 자동조정 엔진, 웹 RBAC, 감사. |
| **agent** | `agent/` | 클러스터당 배포. 수집기(collectors) + 액추에이터(actuators). hub로 outbound 접속. |
| **web** | `web/` | React + TypeScript UI. |
| **proto** | `proto/` | agent↔hub gRPC 계약(정식). Phase 0은 WebSocket 폴백 전송 사용. |
| **deploy** | `deploy/` | k8s 매니페스트(`deploy/k8s/`, kustomize) + Helm 차트 + agent RBAC. |

## 개발 원칙 (보안)

- **실제 클러스터/자격증명을 코드베이스에 입력하지 않는다.** 로컬은 `kind` 또는 `CANGGU_MOCK=1`(합성 데이터)로 개발/검증.
- 생성 kubeconfig는 단기 bound token(`TokenRequest`)만 사용, DB엔 메타데이터만, 세션 인증 후 다운로드, 모든 발급 감사.
- 자동조정은 클러스터/네임스페이스별 **observe-only** 모드로 언제든 개입 중단 가능. 전역 freeze kill-switch 존재.

## 빠른 시작 (로컬, 클러스터 불필요)

```bash
# 1) postgres + redis + hub + (합성 데이터) agent-mock 기동
docker compose up --build      # = make up

# 2) (다른 터미널) 웹 UI dev 서버 — vite 프록시가 hub:8000 로 연결
cd web && npm install && npm run dev    # = make web
open http://localhost:5173
```

컨테이너 없이 개별 실행하려면(각각 다른 터미널):

```bash
make hub          # hub (sqlite, uvicorn --reload)
make agent-mock   # MOCK agent
make web          # vite dev
```

테스트: `make test` (hub 엔진 규칙 + agent 액추에이터 단위 테스트).

## 기능 (구현 완료)

| # | 기능 | 설명 |
|---|------|------|
| 1 | HTTPRoute 트래픽 흐름 | Gateway API HTTPRoute 별 hostname→gateway→route→backend service 흐름 시각화 |
| 2 | 성능치 표시 | Prometheus p95(폴백 metrics-server) usage vs requests/limits |
| 3 | Namespace 구분 | 전 뷰 namespace 필터 + 웹 RBAC 스코프 |
| 4 | Multicluster | hub-spoke agent 모델(클러스터별 등록) |
| 5 | 재기동 Pod | restartCount 실시간 표시 |
| 6 | SVC 헬스 | endpoints ready/desired 기반 정상 여부 |
| 7 | 스토리지 토폴로지 | SC ↔ PV ↔ PVC ↔ 사용 워크로드 연결 |
| 8 | 노드 분산 강제 | 파드 <2노드 워크로드에 topologySpreadConstraints 자동 주입 |
| 9 | kubeconfig 발급 | 네임스페이스별 SA(admin/edit/view) + TokenRequest 단기토큰 다운로드 |
| 10 | Velero 백업/복구 | 네임스페이스별 백업/복구/정기 스케줄 시각화·실행 |

**자동조정 엔진**: 규칙 7종(리소스 우측정렬 · CPU throttling · OOMKilled · CrashLoop · 노드분산 · unschedulable · ImagePullBackOff). `Detector → Issue → SuggestedAction → 모드 게이트 → Command → 감사`. 클러스터/네임스페이스별 **observe(제안만)/active(자동적용)** 모드, 전역 freeze, 규칙별 활성/auto_apply 런타임 설정, 동일 이슈 쿨다운(안티플래핑).

**부가**: 수동 리소스 편집(dry-run), 자동조정 라이브 피드, 로그인 인증 + admin/viewer × 전체클러스터/네임스페이스 스코프 웹 RBAC + 웹 사용자 관리.

## 운영 배포

실 클러스터 배포는 [`deploy/k8s/README.md`](deploy/k8s/README.md) 참고. 요약:

```bash
make images push          # registry.local.cloud:5000 로 hub(웹 포함)+agent 빌드/푸시
kubectl create namespace canggu-system
kubectl -n canggu-system create secret generic canggu-secrets \
  --from-literal=agentToken="$(openssl rand -hex 24)" \
  --from-literal=jwtSecret="$(openssl rand -hex 32)" \
  --from-literal=adminPassword="$(openssl rand -base64 18)"
make deploy                # kubectl apply -k deploy/k8s
```

웹: `https://<gateway-host>/operating/` · admin 비밀번호는 Secret `canggu-secrets.adminPassword`.
자세한 운영 절차(로그인, kubeconfig 발급, Velero, 규칙 설정, 사용자 관리)는 [`docs/operations.md`](docs/operations.md).

## 상태

Phase 0~5 + 하드닝(인증/스코프/쿨다운/Prometheus) 완료. 로드맵·상태는 [`docs/architecture.md`](docs/architecture.md).
