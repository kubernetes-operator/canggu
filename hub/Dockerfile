# 빌드 컨텍스트 = 레포 루트 (docker build -f hub/Dockerfile .)
# 1단계: 웹 SPA 빌드 (base=/operating/)
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG CANGGU_BASE_PATH=/operating/
ENV CANGGU_BASE_PATH=${CANGGU_BASE_PATH}
RUN npm run build

# 2단계: hub 런타임 + 정적 SPA 서빙
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CANGGU_WEB_DIST=/app/web_dist

RUN pip install --no-cache-dir \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "pydantic>=2.6" "pydantic-settings>=2.2" \
    "sqlalchemy>=2.0" "psycopg[binary]>=3.1" "websockets>=12.0"

COPY hub/app ./app
COPY --from=web /web/dist ./web_dist

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
