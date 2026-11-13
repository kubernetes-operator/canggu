FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir \
    "pydantic>=2.6" "pydantic-settings>=2.2" "websockets>=12.0" "kubernetes>=29.0"

COPY app ./app

# 이미지 태그와 같은 값을 넣어 빌드: docker build --build-arg VERSION=<tag>
# hub 에 보고되는 agent_version 이 실제 배포 이미지와 일치하도록 한다.
ARG VERSION=dev
ENV CANGGU_AGENT_VERSION=$VERSION

# 기본은 안전한 MOCK. 실 클러스터 배포 시 CANGGU_MOCK=0 + in-cluster SA.
ENV CANGGU_MOCK=1
CMD ["python", "-m", "app.main"]
