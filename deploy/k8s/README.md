# canggu k8s 배포

`test2.studiobasa.com/operating/` 로 서빙되며 `registry.local.cloud:5000` 이미지를 사용한다.
hub 단일 서비스가 API·WebSocket·웹 SPA 를 모두 서빙한다(gateway 가 `/operating/` prefix strip).

## 1) 이미지 빌드 & 푸시 (레포 루트에서)

```bash
make images        # hub(웹 포함) + agent 빌드
make push          # registry.local.cloud:5000 로 푸시
```

## 2) 시크릿 생성 (커밋 금지)

hub 와 agent 가 공유하는 부트스트랩 토큰:

```bash
kubectl create namespace canggu-system --dry-run=client -o yaml | kubectl apply -f -
kubectl -n canggu-system create secret generic canggu-secrets \
  --from-literal=agentToken="$(openssl rand -hex 24)"
```

## 3) 배포

```bash
kubectl apply -k deploy/k8s      # = make deploy
```

## 4) 확인

```bash
kubectl -n canggu-system get pods,svc,httproute
kubectl -n canggu-system logs deploy/canggu-agent   # hub 접속 로그
curl -sk https://test2.studiobasa.com/operating/healthz
```

브라우저: <https://test2.studiobasa.com/operating/>

## 비고
- DB 는 sqlite(NFS PVC, 단일 replica). Postgres 전환은 `CANGGU_DATABASE_URL` 변경.
- agent 는 in-cluster SA(`canggu-agent`)로 이 클러스터를 관리. RBAC 는 `20-agent-rbac.yaml`.
- 자동조정은 기본 OBSERVE(관찰전용). 웹 UI 또는 API 로 ACTIVE 전환 시에만 실제 변경.
