# canggu 운영 가이드

웹: `https://<gateway-host>/operating/` (예: https://test2.studiobasa.com/operating/)

## 로그인 / 사용자

- 부트스트랩 admin: 사용자 `admin`, 비밀번호는 Secret `canggu-secrets.adminPassword`.
  ```bash
  kubectl -n canggu-system get secret canggu-secrets -o jsonpath='{.data.adminPassword}' | base64 -d
  ```
- **사용자 관리**(admin, "웹 사용자 관리" 패널 또는 API):
  - `POST /api/auth/users` `{username,password,role,scope_type,scope_ref}`
    - role: `admin`(변경 가능) | `viewer`(읽기)
    - scope_type=`cluster`(scope_ref="" 전체 / "<cluster>" 특정) | `namespace`(scope_ref="<cluster>/<ns>")
  - `PATCH /api/auth/users/{username}`(비번/role/scope 변경), `DELETE`(삭제). 자기 자신·마지막 admin 삭제/강등 차단.

## 자동조정 모드 (관여 제어)

- 클러스터/네임스페이스별 **OBSERVE**(제안만) ↔ **ACTIVE**(자동 적용). 유효 모드 = min(cluster, ns).
- 전역 **freeze**: 모든 자동조정 즉시 중단(kill-switch).
- **규칙 설정**("자동조정 규칙 설정" 패널): 규칙별 활성/비활성 + auto_apply(기본/항상자동/항상수동).
- 동일 이슈는 쿨다운(기본 300s) 동안 재적용 억제.
- 제안된 조치는 admin 이 **Dry-run**/**적용** 가능. 워크로드 리소스는 "편집"으로 수동 변경(dry-run 지원).

## kubeconfig 발급 (기능 #9)

"네임스페이스 접근 발급" 패널(admin): NS + role(admin/edit/view) + SA 이름 + TTL → **발급 & 다운로드**.
- agent 가 NS 에 SA + RoleBinding(내장 ClusterRole) 생성, `TokenRequest` 단기 bound token 으로 kubeconfig 생성.
- hub 는 토큰을 저장하지 않음(발급 메타데이터만 감사).

## Velero 백업/복구 (기능 #10)

"백업/복구(Velero)" 패널(설치 시):
- 백업 목록/상태 조회, **이 NS 백업 생성**, 완료 백업에서 **복구**(파괴적 — 확인 필요, 클러스터 admin).
- **정기 백업 스케줄**: NS + cron 으로 생성/삭제.

## API 요약

- 인증: `POST /api/auth/login`, `GET /api/auth/me`, `*/api/auth/users`
- 조회(로그인): `/api/clusters`, `/api/clusters/{c}/{namespaces,pods,workloads,services,storage,routes,issues,velero,...}`
- 변경(admin): `/mode`, `/freeze`, `/namespaces/{ns}/mode`, `/issues/{fp}/apply`, `/workloads/.../patch`, `/kubeconfig`, `/velero/*`, `/api/rules/{id}`
- 라이브 피드: `WS /live/ws?token=<jwt>` (스코프별 필터)

모든 조회·변경은 웹 RBAC 스코프로 서버 측 강제(네임스페이스 사용자는 자기 NS 만).

## 트러블슈팅

- **agent 미접속**: `kubectl -n canggu-system logs deploy/canggu-agent` — hub Service 접속/토큰 확인.
- **metrics 없음**: metrics-server / `CANGGU_PROMETHEUS_URL` 확인. 메트릭 없으면 리소스 규칙은 fail-safe 로 미동작.
- **kubectl 로 velero schedule 조회**: `schedule` 축약명은 fleet.cattle.io 로 해석됨 → `kubectl get schedule.velero.io -n velero`.
- 이미지 재배포: `deploy/k8s/*.yaml` 태그 범프 후 `make images push deploy`.
