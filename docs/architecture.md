# canggu 아키텍처

## 개요

hub-spoke 멀티클러스터. 각 클러스터의 **agent**가 hub로 **outbound** 접속(방화벽 친화적)하여
텔레메트리를 push 하고 명령을 receive 한다. hub 는 집계·분석·자동조정·웹RBAC·감사를 담당하고,
웹 UI 가 이를 권한에 따라 노출한다.

```
┌────────────┐  gRPC bidi stream (Phase 0: WS 폴백)   ┌──────────────────────┐
│  Agent     │ ── telemetry push / command receive ─▶ │  Hub (FastAPI)       │
│ (클러스터당)│ ◀─ Command{patch,dry_run,ttl,sig} ──── │  + Postgres + Redis  │
└────────────┘                                          └──────────┬───────────┘
  in-cluster SA 로 로컬에서만 조회/변경                              │ REST + WS/SSE
  collectors / actuators                                  ┌────────▼─────────┐
                                                          │  Web (React+TS)  │
                                                          └──────────────────┘
```

## agent ↔ hub 프로토콜

- **정식: gRPC 양방향 스트림** (`proto/agent_hub.proto`). protobuf 스키마 강제, 버전 호환, keepalive.
- **Phase 0: WebSocket 폴백** — 동일 메시지 스키마의 JSON 을 `/agentlink/ws` 로 주고받음. codegen 없이 즉시 실행 가능.
- 인증: 부트스트랩 자격 → hub 발급 단기 서명 토큰(운영 시 mTLS 클라이언트 인증서 선호). hub 가 `cluster_id` 고정.
- 재연결: 지수 백오프 + 지터, `Hello.last_command_seq` 로 재동기화.
- 명령 수명주기: `ISSUED → DELIVERED → APPLIED | FAILED | SKIPPED_OBSERVE | SKIPPED_SCOPE | EXPIRED`.
  `command_id`(UUID) 멱등 원장으로 재전달=no-op. 모든 전이는 감사 기록.
- 안전: 명령에 hub 서명 capability token. **agent 는 적용 전 target ns 의 scope+mode 를 재검증**(hub 맹신 금지).

## 자동조정 엔진

`Detector → Issue(fingerprint dedup) → SuggestedAction(patch+risk tier) → Gate(mode) → Command → Result → 해소`

- **모드 게이트**: `effective = min(cluster_mode, ns_mode)`, observe 우선. observe 에서도 탐지·제안은 생성/표시하되 명령 미발행(`SKIPPED_OBSERVE`).
- high-risk tier 는 active 에서도 UI 수동 승인 필요.
- 가드레일: per-rule rate limit / cooldown / hysteresis, quota 사전 체크, dry-run, 전역 freeze kill-switch.
- 규칙 목록은 [`remediation-rules.md`](remediation-rules.md).

## 웹 RBAC vs K8s RBAC

- **웹 RBAC(hub 소유)**: `{subject, scope: cluster|namespace, scope_ref, level: view|admin}`. API 게이트 + 데이터 필터.
- **K8s RBAC(agent SA)**: 실제 변경 권한. hub 는 클러스터 자격 없음. read-only collector SA + 좁은 mutator SA 분리.
- 상세는 [`rbac-model.md`](rbac-model.md).

## 데이터 모델 (Postgres)

`clusters` · `namespaces` · `users` · `web_roles` · `agent_identities` · `issues` · `rules` ·
`suggested_actions` · `commands`(=remediation_audit) · `mode_events` · `kubeconfig_grants` · `velero_operations`.
텔레메트리 스냅샷(인벤토리/메트릭/그래프)은 Redis/단기 캐시(영속 X). Phase 0 는 인메모리 캐시.

## 마일스톤 로드맵

| Phase | 내용 | 기능 |
|-------|------|------|
| **0** | 스캐폴드 + 수직 슬라이스 (agent↔hub, 인벤토리, Pod 목록, 규칙3 E2E) | 3·4 기반 |
| **1** | 관측 코어: 메트릭, 재기동 Pod, SVC 헬스, 라이브 피드 | 2·5·6 |
| **2** | Remediation 엔진 + 모드 토글 + 수동편집 + 감사 UI | 규칙1–4, a·b·c |
| **3** | 웹 RBAC 2단계 + OIDC/JWT + NS별 SA/kubeconfig 발급 | d·9 |
| **4** | 스토리지 토폴로지 + HTTPRoute 흐름도 + anti-affinity 분산 강제 | 7·1·8 |
| **5** | Velero 백업/복구 시각화·실행 | 10 |

## 주요 리스크 & 완화

1. **kubeconfig 다운로드 보안** → TokenRequest 단기 토큰, DB 메타데이터만, 취소, web-admin+감사, 세션 인증 다운로드.
2. **agent SA 과권한** → 최소 verb, read/mutate SA 분리, 적용 전 mode+scope 재검증.
3. **remediation blast radius** → rate limit/cooldown/hysteresis, quota 체크, dry-run, 전역 freeze.
4. **메트릭 가용성 가정** → 소스 옵션 취급, graceful degrade(OOM/crashloop/endpoints 는 API server 기반), stale/부재 시 자동조치 금지(fail-safe).
5. **멀티클러스터 일관성** → resourceVersion 낙관적 동시성, 명령 TTL, agent 텔레메트리를 진실 소스로, 클러스터별 모드 격리.
