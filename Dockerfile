FROM node:24-alpine AS crm-build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt constraints.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=crm-build /web/out ./app/crm_static
RUN useradd --uid 10001 --create-home app && mkdir -p /data/media && chown -R app /srv /data
USER app
# API is published only on the host loopback. Trusting forwarded headers is safe
# because the public internet cannot open this port directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
