# canggu 운영 가이드

웹: `https://<gateway-host>/operating/` (예: https://test2.studiobasa.com/operating/)

## 배포 (GitOps / ArgoCD)

- ArgoCD Application: `deploy/argocd/application.yaml` → `kubectl apply -f`. `deploy/k8s`(kustomize) 추적, 자동 동기화(prune+selfHeal).
- **버전 업**: `deploy/k8s/kustomization.yaml` 의 `images.newTag` 수정 → `make images push` → 커밋/푸시 → ArgoCD 자동 배포.
  - Makefile 의 `TAG`/`AGENT_TAG` 기본값은 kustomization 의 newTag 에서 읽는다 → 배포 태그 = 이미지 태그 = 표시 버전.
  - 이미지는 `--build-arg VERSION=<태그> --build-arg REVISION=<git 커밋>` 으로 빌드(Makefile 이 자동 전달, 생략 시 `dev`). agent 는 버전을 hub 에 보고하고, hub 는 `/healthz` 에 `version`·`revision` 을 노출한다. 커밋되지 않은 변경이 있으면 revision 에 `-dirty`.
  - 외부 도구(web-standard `release-web.py`)는 기존 hub 이미지 위에 웹 결과물만 덮어 커밋 해시 태그(예: `293ff9c2f081`)로 배포한다. 이때 `/healthz` 의 version 은 바탕 hub 버전이다. 다음 정식 릴리스에서 semver 태그로 복귀.
- 시크릿(`canggu-secrets`)은 git 밖 1회 부트스트랩. 직접 `kubectl`/이미지 수정은 selfHeal 로 되돌려지므로 git 을 통해 변경.

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
- **개인 계정 사용 권장**: 모드 변경 이력은 로그인 계정으로 기록된다. 공용 `admin` 으로 바꾸면 누가 바꿨는지 알 수 없으므로 사람마다 개인 admin/viewer 계정을 만들어 쓴다. 공용 `admin` 은 복구·계정 관리용으로만 둔다.
  - 기본 설정(`CANGGU_MODE_ACTIVE_REQUIRE_PERSONAL=true`)에서는 공용 `admin` 계정으로 ACTIVE 전환이 거부된다(403). OBSERVE 로 끄는 것은 허용.
- **본인 비밀번호 변경**(모든 로그인 사용자): 헤더 "비밀번호 변경" 또는 `POST /api/auth/change-password` `{old_password,new_password}`(현재 비번 확인, 6자 이상).

## 자동조정 모드 (관여 제어)

- 클러스터/네임스페이스별 **OBSERVE**(제안만) ↔ **ACTIVE**(자동 적용). 유효 모드 = min(cluster, ns).
- **ACTIVE 안전장치**:
  - 전환 시 웹에서 확인창(드리프트 경고), live 피드 `mode_changed` 에 변경자·자동 복귀 시각, hub 로그 WARNING.
  - **자동 복귀**: ACTIVE 는 `CANGGU_MODE_ACTIVE_MAX_SECONDS`(기본 3600, 0=끔) 뒤 OBSERVE 로 자동 복귀(점검 주기 `CANGGU_MODE_EXPIRY_CHECK_SECONDS`, 기본 60). 변경자 `system:auto-revert` 로 이력에 남는다. 계속 ACTIVE 가 필요하면 다시 켠다(기준 시각 갱신).
  - **모드 변경 이력**: 웹 "모드 변경 이력" 패널 또는 `GET /api/clusters/{id}/mode-events`(클러스터 범위 admin) — 누가·언제·무엇을, 현재 ACTIVE 범위와 복귀 예정 시각.
- 전역 **freeze**: 모든 자동조정 즉시 중단(kill-switch).
- **규칙 설정**("자동조정 규칙 설정" 패널): 규칙별 활성/비활성 + auto_apply(기본/항상자동/항상수동).
- 동일 이슈는 쿨다운(기본 300s) 동안 재적용 억제.
- **자동 적용 제외 대상**(ACTIVE 에서도 `manual_required`):
  - GitOps 관리 리소스: agent 가 ArgoCD(`argocd.argoproj.io/tracking-id` 주석 또는 managedFields `argocd-controller`)·Flux(라벨/managedFields)를 감지. 직접 patch 하면 selfHeal 이 되돌리므로 제안에 "git 매니페스트에서 수정" 안내, 웹에 `argocd`/`flux` 배지, 수동 적용 시 경고
  - StatefulSet(수동 적용만), DaemonSet(진단 전용)
- **반복 적용 차단(플래핑 브레이커)**: 같은 대상·규칙의 엔진 자동 적용이 `CANGGU_REMEDIATION_FLAP_WINDOW_SECONDS`(기본 3600) 안에 `CANGGU_REMEDIATION_FLAP_THRESHOLD`(기본 3)회 이상이면 자동 적용 중단 → disposition `flapping`("반복 적용 차단"), 라이브 피드 `flapping_blocked`. 감사 원장(DB) 기준이라 hub 재시작에도 유지. 원인(다른 컨트롤러의 되돌림)을 해소하고 창이 지나면 자동 해제
- 제안된 조치는 admin 이 **Dry-run**/**적용** 가능. 워크로드 리소스는 "편집"으로 수동 변경(dry-run 지원).

## kubeconfig 발급 (기능 #9)

"네임스페이스 접근 발급" 패널(admin): NS + role(admin/edit/view) + SA 이름 + TTL → **발급 & 다운로드**.
- agent 가 NS 에 SA + RoleBinding(내장 ClusterRole) 생성, `TokenRequest` 단기 bound token 으로 kubeconfig 생성.
- hub 는 토큰을 저장하지 않음(발급 메타데이터만 감사).

## Velero 백업/복구 (기능 #10)

"백업/복구(Velero)" 패널(설치 시):
- 백업 목록/상태 조회, **이 NS 백업 생성**, 완료 백업에서 **복구**(파괴적 — 확인 필요, 클러스터 admin).
- **정기 백업 스케줄**: NS + cron 으로 생성/삭제.

## Operating 뷰 (워크로드 통합 상태)

대시보드 상단 패널. 워크로드별로 Pod 상태·p95 사용량·경고 이벤트·이슈(제안)를 한 행에 모으고, 행을 펼치면 상세와 조치 버튼이 나온다. API: `GET /api/clusters/{c}/operating?namespace=`.

- 상태: **위험**(critical 이슈 또는 Ready 0) · **주의**(이슈, 경고 이벤트, Ready 부족, 문제 Pod) · **정상**
- agent 는 Deployment·StatefulSet·DaemonSet 스펙을 수집한다. Job 등 그 외 소유자는 Pod 기준으로 추정(`Pod 기준` 배지, 리소스 미표시)
- 종류별 조치 범위: Deployment = 자동(ACTIVE)·수동 적용 / StatefulSet = 수동 적용만(ACTIVE 에서도 `manual_required`, 롤링 재시작 영향) / DaemonSet = 진단 전용(agent 에 patch 권한 없음, 편집 버튼 없음)
- 노이즈 억제(hub 설정):
  - `CANGGU_EVENT_NOISE_REASONS` (기본 `DNSConfigForming`): 상태 판정·경고 수에서 제외할 이벤트 reason, 콤마 구분. 상세에는 흐리게 표시
  - `CANGGU_RESTART_RECENT_WINDOW_SECONDS` (기본 3600): 이 시간 안에 restartCount 가 **증가한** Pod 만 문제로 본다. 누적값(과거 노드 재부팅 등)은 무시. hub 가 스냅샷 간 증가를 추적하므로 hub 재시작 직후에는 이력이 비어 있다

## API 요약

- 인증: `POST /api/auth/login`, `GET /api/auth/me`, `*/api/auth/users`
- 조회(로그인): `/api/clusters`, `/api/clusters/{c}/{namespaces,pods,workloads,services,storage,routes,issues,velero,...}`
- 변경(admin): `/mode`, `/freeze`, `/namespaces/{ns}/mode`, `/issues/{fp}/apply`, `/workloads/.../patch`, `/kubeconfig`, `/velero/*`, `/api/rules/{id}`
- 라이브 피드: `WS /live/ws?token=<jwt>` (스코프별 필터)
- **대시보드 묶음**: `GET /api/clusters/{c}/dashboard?namespace=` — 웹 새로고침 1회에 필요한 13개 조회(clusters·pods·operating·summary 등)를 한 응답으로. 섹션 실패는 `errors` 에 기록되고 값은 null.

## 성능 메모

- 외부 주소(Cloudflare 경유)는 요청당 왕복 0.5~0.9s, 내부 LB 직접은 0.01~0.1s (2026-11-17 측정). 체감 지연의 대부분은 경로 비용이라 요청 수를 줄이는 것이 핵심.
- 웹: 새로고침은 묶음 API 1회, 탭이 숨겨져 있으면 주기 갱신 중단, 라이브 이벤트 갱신은 1초 단위로 합침.
- 정적 자산: `assets/*`(해시 파일명) `Cache-Control: public, max-age=31536000, immutable`, `index.html` 은 `no-cache`.
- 진단: 모든 HTTP 응답에 `Server-Timing`(`app` = hub 처리 시간, 묶음 API 는 느린 섹션 `s-<이름>` 추가). 브라우저 개발자 도구 Network → Timing 에서 서버 시간과 네트워크 시간을 구분.

모든 조회·변경은 웹 RBAC 스코프로 서버 측 강제(네임스페이스 사용자는 자기 NS 만).

## 트러블슈팅

- **agent 미접속**: `kubectl -n canggu-system logs deploy/canggu-agent` — hub Service 접속/토큰 확인.
- **metrics 없음**: metrics-server / `CANGGU_PROMETHEUS_URL` 확인. 메트릭 없으면 리소스 규칙은 fail-safe 로 미동작.
- **kubectl 로 velero schedule 조회**: `schedule` 축약명은 fleet.cattle.io 로 해석됨 → `kubectl get schedule.velero.io -n velero`.
- 이미지 재배포: `deploy/k8s/*.yaml` 태그 범프 후 `make images push deploy`.
