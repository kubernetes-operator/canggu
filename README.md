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
| **deploy** | `deploy/` | Helm 차트 + agent RBAC 스켈레톤. |

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

## 현재 상태

**Phase 0 (스캐폴드 + 수직 슬라이스)** 구현:
- monorepo 레이아웃, agent↔hub 링크(WebSocket 전송), 클러스터 레지스트리
- 인벤토리 스트리밍 → 웹의 namespace 필터 Pod 목록
- 자동조정 규칙 #3(리소스 우측정렬) 엔드투엔드: observe 모드에선 "제안"만, active 모드에선 실제 patch + 감사 로그
- 클러스터/NS 모드 토글, 자동조정 라이브 피드

이후 Phase 1~5 로드맵은 `docs/architecture.md` 참고.
