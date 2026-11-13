# 랩 클러스터 리소스 기준선 (canggu 자동조정 드리프트 반영)

랩 클러스터가 ACTIVE 모드였을 때 canggu 엔진이 자동으로 바꾼 값입니다. 2026-11-12 기준 라이브에 남아 있는
값(주로 BestEffort Pod에 새로 넣은 requests, 일부 Pod 분산 설정)을 원천으로 옮겨 적었습니다.
목적은 이 값이 "누가 왜 넣었는지 모르는 드리프트"로 남지 않도록 하는 것입니다.

> 이 디렉터리의 파일을 커밋해도 클러스터는 바뀌지 않습니다. 이 파일들은 다음에 재설치하거나
> 업그레이드할 때 같은 값을 유지하기 위한 기록입니다.

## 구성

| 원천 | 위치 | 대상 |
|---|---|---|
| Helm values 오버레이 | `helm/<release>.yaml` | arc, cilium(operator·hubble-relay·hubble-ui), gitea, headlamp, loki-stack(grafana), prometheus-stack(grafana·operator·kube-state-metrics) |
| Strategic merge patch | `patches/<ns>--<name>.yaml` | argocd 6개, cert-manager 3개, snapshot-controller, nginx-gateway, open-webui, iperf3 client/server, echo-backend |
| 각 앱 저장소 (별도 브랜치) | velero values, model-portal 매니페스트, os-monitor base 매니페스트 | velero, model-portal, os-monitor-api/dashboard |

## 사용법 (랩 변경 작업이므로 실행 전에 승인을 받습니다)

Helm: 재설치하거나 업그레이드할 때 기존 values 뒤에 오버레이를 붙입니다.

```bash
helm get values <release> -n <ns> -o yaml > /tmp/uv.yaml
helm upgrade <release> <chart> --version <현재 버전> -n <ns> -f /tmp/uv.yaml -f helm/<release>.yaml
```

kubectl로 설치한 것: upstream 매니페스트(argocd install.yaml, cert-manager.yaml 등)를 다시 적용했다면 그 직후에 패치를 적용합니다.

```bash
kubectl patch deploy <name> -n <ns> --type strategic --patch-file patches/<ns>--<name>.yaml
```

## 검증 기록
- Helm 7개 차트(정확한 차트 버전): `현재 user values + 오버레이`로 `helm template`을 렌더링해 라이브 Deployment의 resources와 비교했고 일치했습니다.
  - 예외: prometheus-stack grafana sidecar. dashboard와 datasources 두 컨테이너가 같은 값을 공유하는 구조라서, 둘 중 큰 값(10m/90Mi)으로 맞췄습니다. 그래서 datasources 컨테이너는 1Mi 차이가 납니다.
- 패치 파일은 라이브 스펙에서 생성했습니다.
- os-monitor는 prod overlay를 `kubectl kustomize`로 빌드해 라이브와 비교했고 일치했습니다.

## 주의
- 값은 canggu가 p95 사용량에서 산출한 것입니다. cpu 2m, iperf3 memory 2Mi처럼 매우 작은 값이 있습니다.
  - limits가 없으므로 OOM 위험은 늘지 않습니다. 스케줄링 보장이 작다는 뜻일 뿐입니다.
  - 부하 테스트용 iperf3는 기준선으로 둘 필요가 없으면 패치를 지워도 됩니다.
- hubble-ui backend(1176Mi)와 open-webui(1067Mi)는 memory requests가 큽니다. 노드 여유를 고려해 검토하세요.
