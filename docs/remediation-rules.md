# 자동조정 규칙

각 규칙: `Detector(텔레메트리 조건) → Issue → SuggestedAction(patch + risk tier) → Gate(mode) → Command`.
observe-only 에서는 탐지·제안까지만. high-risk 는 active 에서도 수동 승인.

| # | id | 탐지 | 수정(action) | risk | auto_apply |
|---|----|------|------|------|-----------|
| 1 | `oom-killed` | container lastState.terminated.reason=OOMKilled, 창 내 ≥N | 메모리 limit 단계 증가(+50%, quota/정책 상한) | med | 정책 |
| 2 | `cpu-throttling` | `container_cpu_cfs_throttled_periods` 지속 초과 | CPU limit 상향 또는 request-only 전환 | med | 정책 |
| 3 | `resource-rightsize` | 사용량 p95 ≫ requests, 또는 limit 미설정 | requests 를 p95 로 우측정렬, limit=배수 | low | ✅ |
| 4 | `crashloop` | restartCount 상승, waiting.reason=CrashLoopBackOff | 무조건 재시작 금지. 로그/이벤트 표면화 + last-good ReplicaSet 롤백 옵션 | high | ❌(수동) |
| 5 | `pod-spread` | 파드가 걸친 distinct node < 2 (기능8) | 소유 컨트롤러 PodTemplate 에 topologySpreadConstraints 주입 | med | 정책 |
| 6 | `unschedulable` | FailedScheduling: Insufficient cpu/mem | 우측정렬로 해소 가능 시만 자동 | med | 정책 |
| 7 | `svc-no-endpoints` | ready endpoint 0 while desired ≥1 | 알림 + 실패 Pod 상관 (진단, 자동변경 없음) | info | ❌ |
| 8 | `image-pull-backoff` | waiting.reason=ImagePullBackOff | 표면화(태그/레지스트리 인증) | warn | ❌ |

**구현 완료**: 규칙 7종 모두 동작(#1 oom-killed, #2 cpu-throttling, #3 resource-rightsize, #4 crashloop(진단), #5 pod-spread, #6 unschedulable(진단), #8 image-pull-backoff(진단)). 규칙별 활성/auto_apply 는 웹에서 런타임 설정(`/api/rules`). 동일 이슈(fingerprint)는 쿨다운(기본 300s) 동안 자동 재적용 억제(안티플래핑). 메트릭 소스는 Prometheus p95(폴백 metrics-server); 메트릭 없으면 리소스 규칙은 fail-safe 로 미동작.

## 규칙 #3 상세 (핵심 "성능 부족" 자동 수정)

- **탐지**: 컨테이너에 requests 미설정이거나, `usage_p95 > requests * factor`(기본 1.5).
- **제안 patch** (strategic-merge, Deployment):
  ```json
  {"spec":{"template":{"spec":{"containers":[
    {"name":"<c>","resources":{"requests":{"cpu":"<p95cpu>","memory":"<p95mem>"}}}
  ]}}}}
  ```
- **가드레일**: 네임스페이스 ResourceQuota 초과 시 상한으로 clamp, 미해결이면 제안만. cooldown 후 재평가.
- **모드**: observe → 제안만(`SKIPPED_OBSERVE`). active → dry-run 미리보기 후 적용, before/after resourceVersion 감사.

## 안티플래핑 / blast radius

- `cooldown`(규칙별), `max_actions_per_window`, hysteresis(임계 상/하한 분리).
- 전역 `remediation_frozen` kill-switch (ClusterConfig 로 하달).
- 적용 전 `expected_resource_version` 검증 → 불일치 시 거부(stale 방지).
