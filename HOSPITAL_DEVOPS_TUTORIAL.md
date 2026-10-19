# From FastAPI to Production: Building a Hospital Management Platform with Docker, Jenkins, and Observability

A practical B.Tech project guide to automated deployment, caching, health checks, and live metrics.

## Introduction

A local application can appear reliable while running in a single terminal: start the API, connect to a database, and test one request. Production changes the problem. Traffic becomes unpredictable, deployments need to be repeatable, failures must be visible, and slow database reads can quickly become a user-facing issue.

This project addresses those concerns with a small but realistic platform for a Hospital Management System. FastAPI provides the backend API, Nginx acts as the public entry point, Redis caches frequently requested patient data, PostgreSQL stores durable records, Prometheus collects metrics, and Grafana turns those metrics into operational dashboards. Docker Compose runs the six services together, while Jenkins automates the build and deployment pipeline as code.

The result is not merely a CRUD demo. It is a compact production-style environment that demonstrates the engineering habits recruiters and platform teams look for: separation of concerns, reproducible environments, health checks, observable services, and an automated delivery path.

## Architecture at a Glance

```text
                                      +----------------------+
                                      |       Grafana        |
                                      | dashboards and alerts|
                                      +----------^-----------+
                                                 |
                                                 | Prometheus queries
                                                 |
+--------+       +---------+       +-------------+-------------+
| User   | ----> |  Nginx  | ----> |       FastAPI API        |
| client |       | gateway |       | patients, doctors, health|
+--------+       +---------+       +-------+-------------+------+
                                           |             |
                                   cache hit|             | durable reads/writes
                                           v             v
                                      +---------+     +-----------+
                                      |  Redis  |     | PostgreSQL|
                                      | cache   |     | database  |
                                      +---------+     +-----------+
                                           ^
                                           |
                                  metrics exposed by API
                                           |
                                      +----+-----+
                                      |Prometheus|
                                      | scraping |
                                      +----------+
```

The user reaches only Nginx. Nginx forwards API traffic to FastAPI, while FastAPI decides whether a request can be served from Redis or must read PostgreSQL. Prometheus periodically scrapes the API's `/metrics` endpoint. Grafana reads Prometheus data and displays request volume, latency, and service health.

## The Four Features That Make This Project Production-Oriented

### 1. API gateway routing

Nginx provides one stable public endpoint and forwards requests to the internal API container. This gives the system a natural place to add TLS termination, rate limiting, access logs, compression, and load balancing later without putting those concerns inside FastAPI.

### 2. Redis-backed response caching

Patient listings are a good cache candidate because they are read frequently and do not change on every request. The API checks Redis first. On a cache hit, it avoids a database round trip and can return the serialized response in milliseconds. A short time-to-live prevents stale data from living forever.

### 3. Database and service health checks

A container being "running" does not prove that its application is ready. Docker Compose health checks verify PostgreSQL with `pg_isready`, Redis with `redis-cli ping`, and the API with `/health`. This makes startup behavior and failure diagnosis much clearer.

### 4. Live traffic monitoring

Prometheus records request counters and latency histograms. Grafana then makes those signals visible while the application is running. This turns an otherwise invisible performance regression into a measurable event.

## Implementation Guide

### What each file does

- **`docker-compose.yml`** defines and connects the six services, their ports, volumes, environment variables, dependencies, and health checks.
- **`main.py`** defines the FastAPI endpoints, initializes Redis and PostgreSQL clients, implements the cache-aside read path, and exposes health and Prometheus endpoints.
- **`nginx.conf`** receives external HTTP traffic and proxies `/` to the internal `hospital-app:8000` service.
- **`prometheus.yml`** tells Prometheus which target to scrape and how often to collect metrics.
- **`Dockerfile`** packages the FastAPI application into a reproducible image.
- **`Jenkinsfile`** describes the checkout, image build, and deployment stages as Pipeline as Code.

### Six-service Docker Compose reference

The following is a reference configuration for the complete architecture. The service names are important: Docker Compose places them on a shared network, so the API connects to `redis:6379` and `postgres:5432` rather than `localhost`.

```yaml
services:
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      hospital-app:
        condition: service_healthy

  hospital-app:
    build: .
    environment:
      DATABASE_URL: postgresql://hospital:hospital@postgres:5432/hospital
      REDIS_URL: redis://redis:6379/0
    expose:
      - "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: hospital
      POSTGRES_USER: hospital
      POSTGRES_PASSWORD: hospital
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U hospital -d hospital"]
      interval: 5s
      timeout: 5s
      retries: 5

  prometheus:
    image: prom/prometheus:v2.54.1
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    depends_on:
      hospital-app:
        condition: service_healthy

  grafana:
    image: grafana/grafana:11.2.0
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - prometheus

volumes:
  redis-data:
  postgres-data:
  grafana-data:
```

For a real deployment, replace the example database password with a secret managed by the deployment platform. Exposing only Nginx's port keeps the API, Redis, and PostgreSQL private to the Compose network.

### FastAPI cache and health-check implementation

This example uses `redis.asyncio` and `asyncpg` so network I/O does not block the FastAPI event loop. The cache-aside flow is deliberately simple: read Redis, fall back to PostgreSQL, then cache the result for 60 seconds.

```python
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hospital:hospital@postgres:5432/hospital")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PATIENTS_CACHE_KEY = "patients:v1"
PATIENTS_CACHE_TTL_SECONDS = 60

redis_client: Redis
postgres_pool: asyncpg.Pool


@asynccontextmanager
async def lifespan(_: FastAPI):
    global redis_client, postgres_pool
    redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    postgres_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    yield
    await redis_client.aclose()
    await postgres_pool.close()


app = FastAPI(title="Hospital Management Application", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    try:
        await redis_client.ping()
        async with postgres_pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dependency check failed") from exc
    return {"status": "ok"}


@app.get("/patients")
async def get_patients() -> list[dict[str, Any]]:
    cached_patients = await redis_client.get(PATIENTS_CACHE_KEY)
    if cached_patients:
        return json.loads(cached_patients)

    async with postgres_pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, name, age FROM patients ORDER BY id"
        )

    patients = [dict(row) for row in rows]
    await redis_client.setex(
        PATIENTS_CACHE_KEY,
        PATIENTS_CACHE_TTL_SECONDS,
        json.dumps(patients),
    )
    return patients


Instrumentator().instrument(app).expose(app)
```

In a full application, patient writes should invalidate or refresh `patients:v1`; otherwise a newly created patient may not appear until the TTL expires. The health endpoint checks both dependencies and returns HTTP 503 when either one is unavailable, which gives Docker and an orchestrator a meaningful readiness signal.

### Nginx and Prometheus configuration

A minimal `nginx.conf` can proxy traffic to the Compose service name:

```nginx
server {
    listen 80;

    location / {
        proxy_pass http://hospital-app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Prometheus can scrape the instrumented API every five seconds:

```yaml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: hospital_app
    metrics_path: /metrics
    static_configs:
      - targets: ["hospital-app:8000"]
```

## Running the Environment

From the project directory, build the application image and start the stack in detached mode:

```bash
docker compose up --build -d
```

Check whether all services are running and healthy:

```bash
docker compose ps
```

Useful endpoints are:

- `http://localhost/health` for the API health check through Nginx
- `http://localhost/patients` for the patient listing
- `http://localhost:9090` for Prometheus
- `http://localhost:3000` for Grafana

To inspect application logs while troubleshooting:

```bash
docker compose logs -f hospital-app
```

## Verification and Monitoring

### Measuring the Redis cache

Run the same request twice and compare the timings:

```bash
curl -s -o /dev/null -w 'first request: %{time_total}s\n' http://localhost/patients
curl -s -o /dev/null -w 'second request: %{time_total}s\n' http://localhost/patients
```

The first request is a cache miss: it reads PostgreSQL, serializes the result, and stores it in Redis. The second request is a cache hit and should normally be faster, especially when database latency is non-trivial. Exact numbers depend on the machine, data size, and network conditions, so the meaningful result is the relative improvement rather than a guaranteed millisecond value.

### Building the Grafana view

Add Prometheus as a Grafana data source using `http://prometheus:9090`. A useful first panel for traffic volume is:

```promql
sum(http_requests_total)
```

For a more useful rate over time, use:

```promql
sum(rate(http_requests_total[1m]))
```

A practical dashboard can include request rate, 95th-percentile latency, HTTP status-code distribution, and the up/down state of the API target. After requesting `/patients` repeatedly, the traffic panel should rise, while latency panels can be compared across cache misses and cache hits.

## What Jenkins Automates

The `Jenkinsfile` expresses the delivery workflow as code:

1. Jenkins checks out the repository.
2. Docker builds a fresh `hospital-app` image.
3. The existing Compose deployment is stopped gracefully.
4. The new stack is started in detached mode.
5. The pipeline reports success or failure.

For production hardening, the next version should add automated tests, image vulnerability scanning, a registry push, deployment approvals, and a rollback strategy. The core idea remains the same: every deployment should be repeatable and visible in version control.

## Conclusion and Next Steps

This Hospital Management System demonstrates how a student project can model the concerns of a production platform: gateway routing, separated data services, caching, health-aware startup, metrics collection, dashboards, and automated deployment. The architecture is intentionally small enough to understand but structured around the same principles used by larger engineering organizations.

It is not a claim that a six-container Compose stack has the scale of Netflix or Uber. The useful comparison is architectural thinking: isolate responsibilities, automate delivery, measure real behavior, and design failure detection into the system. From here, the project could evolve toward Kubernetes, managed PostgreSQL and Redis, HTTPS with automated certificates, centralized logs, OpenTelemetry tracing, autoscaling, and blue-green or canary releases.

That progression is the real value of the project. It shows not only that an API can be built, but that the API can be packaged, deployed, observed, and improved like a service that other people depend on.
