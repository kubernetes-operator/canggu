# 권한 모델

두 개의 독립적 계층. 절대 혼동하지 않는다.

## 1. 웹 RBAC (hub 소유)

플랫폼 UI/API 에서 "누가 무엇을 보고 할 수 있는가".

```
Grant = { subject, scope: cluster | namespace, scope_ref, level: view | admin }
```

- `scope=cluster, scope_ref=<cluster_id>` → 해당 클러스터 전체.
- `scope=namespace, scope_ref=<cluster_id>/<ns>` → 특정 네임스페이스만.
- `level=view` → 읽기. `level=admin` → 명령 발행/모드 토글/kubeconfig 발급 가능.
- API 엔드포인트 게이트 + 집계 데이터 필터. NS-view 사용자는 타 NS 를 보지도 못함.

## 2. Kubernetes RBAC (agent SA)

실제 클러스터 변경 권한. **hub 는 클러스터 자격을 갖지 않는다.**

- **collector SA** (read-only): get/list/watch — pods, deployments, services, endpoints, pv, pvc, storageclasses, httproutes, events, nodes.
- **mutator SA** (좁게 스코프): patch deployments/statefulsets(우측정렬·spread), create serviceaccounts/roles/rolebindings + TokenRequest(기능9), velero backups/restores(기능10).
- agent 는 명령 적용 전 **mode + scope 재검증**(hub 맹신 금지). 서명 capability token 검증.

`deploy/rbac/` 에 스켈레톤 매니페스트.

## 매핑

```
web-admin 액션(UI)
  → hub: 웹RBAC 인가 (이 subject 가 이 scope 에 admin?)
  → hub: mode gate (effective = min(cluster, ns))
  → agent: Command 수신, capability_sig 검증, scope+mode 재검증
  → agent: mutator SA 로 적용
  → CommandResult → 감사 기록
```

## 기능9: 네임스페이스 kubeconfig 발급 (별도 K8s RBAC 산출물)

1. 웹-admin 이 NS + 레벨(admin/view) 선택.
2. hub 인가 → agent 에 발급 명령.
3. agent: NS 에 SA + RoleBinding(admin/view ClusterRole 을 NS 스코프 바인딩) 생성.
4. agent: `TokenRequest` API 로 **단기 bound token** 발급(장기 Secret 토큰 금지).
5. kubeconfig 조립 → hub 로 반환 → **세션 인증된 다운로드**(영속 링크 없음).
6. DB 엔 메타데이터만(`kubeconfig_grants`: sa_name, role, expiry, issued_by, revoked_at). 토큰 자체는 저장 안 함.
7. 모든 발급/취소 감사.
