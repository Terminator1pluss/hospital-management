# Automated Deployment and Monitoring of a Hospital Management Application

A FastAPI hospital API packaged with Docker, run as a 6-container stack with Docker Compose,
and deployed to Render as Infrastructure as Code (`render.yaml`).

## Architecture

```text
                 Local: docker compose up
 ┌──────────────────────────────────────────────────────────────┐
 │                                                              │
 │  User ──▶ Nginx :80 ──▶ FastAPI :8000 ──▶ Redis (cache, 30s) │
 │                            │      └─────▶ PostgreSQL         │
 │                            │ /metrics                        │
 │                     Prometheus :9090 ──▶ Grafana :3000       │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘

                 Cloud: Render Blueprint (render.yaml)
 ┌──────────────────────────────────────────────────────────────┐
 │  User ──▶ Render HTTPS ──▶ FastAPI (Docker) ──▶ Key Value    │
 │                                    └──────────▶ PostgreSQL   │
 └──────────────────────────────────────────────────────────────┘
```

| Service | Image | Role | Host port |
|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | API gateway / reverse proxy | 80 |
| `hospital-app` | built from `Dockerfile` | FastAPI backend | internal only |
| `postgres` | `postgres:16-alpine` | Patient and doctor records | internal only |
| `redis` | `redis:7-alpine` | Cache for `/patients` | internal only |
| `prometheus` | `prom/prometheus:v2.54.1` | Scrapes `/metrics` every 5s | 9090 |
| `grafana` | `grafana/grafana:11.2.0` | Dashboards (Prometheus pre-connected) | 3000 |

### Request flow for `/patients`

1. Nginx receives the request on port 80 and forwards it to `hospital-app:8000`.
2. The app checks Redis for the `patients` key.
   - **Cache miss:** reads PostgreSQL, stores the result in Redis for 30 seconds, responds with `X-Cache: MISS`.
   - **Cache hit:** returns the Redis copy, responds with `X-Cache: HIT`.
3. `prometheus-fastapi-instrumentator` counts the request; Prometheus scrapes it; Grafana graphs it.

## Project files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app: routes, Redis caching, health check, metrics, table creation and sample data |
| `Dockerfile` | Builds the app image |
| `docker-compose.yml` | Runs all 6 services with health checks |
| `nginx.conf` | Reverse proxy to the app |
| `prometheus.yml` | Scrape targets (local app, optional Render app) |
| `grafana/datasource.yml` | Adds Prometheus to Grafana automatically |
| `render.yaml` | Render Blueprint (Infrastructure as Code) |
| `Jenkinsfile` | Build and deploy pipeline for a Jenkins server |
| `requirements.txt` | Python dependencies |

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/patients` | Patient list (cached in Redis for 30s) |
| GET | `/doctors` | Doctor list |
| GET | `/health` | `200` if PostgreSQL and Redis respond, otherwise `503` |
| GET | `/metrics` | Prometheus metrics |
| GET | `/docs` | Swagger UI |

## Run locally

Requires Docker Desktop (or Docker Engine with the Compose plugin).

```bash
docker compose up --build -d
docker compose ps        # all services should be Up; app, postgres, redis show (healthy)
```

| URL | What |
|---|---|
| http://localhost/docs | API docs (through Nginx) |
| http://localhost/patients | Patient list |
| http://localhost:9090/targets | Prometheus: `hospital_app` should be **UP** |
| http://localhost:3000 | Grafana (login `admin` / `admin`) |

Stop the stack with `docker compose down`, or `docker compose down -v` to also delete the database and Grafana data.

### Show the cache working

```bash
curl -i http://localhost/patients   # X-Cache: MISS  (read from PostgreSQL)
curl -i http://localhost/patients   # X-Cache: HIT   (served from Redis)
```

After 30 seconds the cache expires and the next request is a `MISS` again.

### Build a Grafana panel

1. Open Grafana → **Dashboards → New → New dashboard → Add visualization**.
2. Pick the **Prometheus** data source (already configured).
3. Enter a query, for example:
   - `sum(http_requests_total)`: total requests
   - `sum by (handler) (rate(http_requests_total[1m]))`: requests per second per endpoint
4. Save the dashboard, then hit `/patients` a few times and watch the graph rise.

`/metrics` and `/health` are excluded from these counts so the graphs show real API traffic only.

## Deploy to Render (Infrastructure as Code)

`render.yaml` describes the cloud setup. Render reads it and creates:

| Resource | Type | Plan |
|---|---|---|
| `hospital-app` | Docker web service, built from `Dockerfile` | free |
| `hospital-db` | Managed PostgreSQL | free |
| `hospital-cache` | Key Value (Redis-compatible) | free |

`DATABASE_URL` and `REDIS_URL` are passed to the app automatically by the Blueprint.
Nginx, Prometheus and Grafana are not deployed: Render's own load balancer handles routing and HTTPS.

Steps:

1. Push this repository to GitHub.
2. In the Render dashboard: **New → Blueprint**, connect the repository and choose the branch.
3. Click **Apply**. Render creates the three resources and deploys the app.
4. Open `https://<your-app>.onrender.com/docs`.

Every push to the connected branch redeploys automatically.

### Monitor the Render deployment locally

In `prometheus.yml`, uncomment the `hospital_app_render` job and replace `your-app-name.onrender.com`
with your app's hostname, then run `docker compose restart prometheus`. Grafana can now graph traffic
from the cloud deployment.

## CI/CD

- **Render:** continuous deployment on every `git push` to the connected branch.
- **Jenkins:** `Jenkinsfile` checks out the code, builds the image and runs `docker compose up -d` on the Jenkins machine.

## Notes

- Free-tier Render web services sleep after about 15 minutes without traffic; the first request then takes up to a minute.
- Free Render PostgreSQL databases expire after 30 days.
- The PostgreSQL password in `docker-compose.yml` is for local use only. On Render, credentials are generated and injected by the Blueprint.
