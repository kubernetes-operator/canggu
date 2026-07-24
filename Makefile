.PHONY: help up down hub agent-mock web test test-hub test-agent proto lint images push deploy undeploy

REGISTRY ?= registry.local.cloud:5000
TAG ?= 0.1.1

help:
	@echo "canggu 개발 타깃:"
	@echo "  make up          - docker compose (postgres, redis, hub, agent-mock) 기동"
	@echo "  make down        - compose 정지"
	@echo "  make hub         - hub 로컬 실행 (sqlite, uvicorn reload)"
	@echo "  make agent-mock  - agent 로컬 실행 (MOCK, 클러스터 불필요)"
	@echo "  make web         - web dev 서버 (vite)"
	@echo "  make test        - hub + agent 단위 테스트"
	@echo "  make proto       - gRPC stub 생성 (grpcio-tools 필요)"

up:
	docker compose up --build

down:
	docker compose down -v

hub:
	cd hub && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

agent-mock:
	cd agent && CANGGU_MOCK=1 CANGGU_CLUSTER_ID=demo CANGGU_CLUSTER_TOKEN=dev-token \
		CANGGU_HUB_URL=ws://localhost:8000/agentlink/ws python -m app.main

web:
	cd web && npm install && npm run dev

test: test-hub test-agent

test-hub:
	cd hub && python -m pytest -q

test-agent:
	cd agent && python -m pytest -q

proto:
	cd proto && python -m grpc_tools.protoc -I. \
		--python_out=../hub/app/gen --grpc_python_out=../hub/app/gen agent_hub.proto

lint:
	cd hub && ruff check app && cd ../agent && ruff check app

# ── 컨테이너 이미지 / k8s 배포 ────────────────────────────────────────────────
images:
	docker build -f hub/Dockerfile -t $(REGISTRY)/canggu/hub:$(TAG) .
	docker build -f agent/Dockerfile -t $(REGISTRY)/canggu/agent:$(TAG) ./agent

push:
	docker push $(REGISTRY)/canggu/hub:$(TAG)
	docker push $(REGISTRY)/canggu/agent:$(TAG)

deploy:
	kubectl apply -k deploy/k8s

undeploy:
	kubectl delete -k deploy/k8s --ignore-not-found
