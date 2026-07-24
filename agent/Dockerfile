FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir \
    "pydantic>=2.6" "pydantic-settings>=2.2" "websockets>=12.0" "kubernetes>=29.0"

COPY app ./app

# 기본은 안전한 MOCK. 실 클러스터 배포 시 CANGGU_MOCK=0 + in-cluster SA.
ENV CANGGU_MOCK=1
CMD ["python", "-m", "app.main"]
