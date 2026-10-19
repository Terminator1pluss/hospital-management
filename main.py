import json
import os
from contextlib import asynccontextmanager
from typing import Dict, List, Union

import asyncpg
from fastapi import FastAPI, HTTPException, Response
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hospital:hospital@postgres:5432/hospital")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PATIENTS_CACHE_KEY = "patients"
PATIENTS_CACHE_TTL_SECONDS = 30

# Runs on every startup; IF NOT EXISTS / WHERE NOT EXISTS make it safe to repeat.
# Done here (not an init.sql) so it also works on Render's managed Postgres.
SCHEMA_AND_SEED = """
CREATE TABLE IF NOT EXISTS patients (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    age INT NOT NULL
);
CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT NOT NULL
);
INSERT INTO patients (name, age)
SELECT * FROM (VALUES ('Alice Johnson', 34), ('Michael Smith', 52)) AS seed
WHERE NOT EXISTS (SELECT 1 FROM patients);
INSERT INTO doctors (name, specialty)
SELECT * FROM (VALUES ('Dr. Emily Carter', 'Cardiology'), ('Dr. James Wilson', 'Neurology')) AS seed
WHERE NOT EXISTS (SELECT 1 FROM doctors);
"""

db_pool: asyncpg.Pool
redis_client: Redis


@asynccontextmanager
async def lifespan(_: FastAPI):
    global db_pool, redis_client
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    await db_pool.execute(SCHEMA_AND_SEED)
    yield
    await redis_client.aclose()
    await db_pool.close()


app = FastAPI(title="Hospital Management Application", lifespan=lifespan)


@app.get("/health")
async def health() -> Dict[str, str]:
    try:
        await redis_client.ping()
        await db_pool.fetchval("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dependency check failed") from exc
    return {"status": "ok"}


@app.get("/patients")
async def get_patients(response: Response) -> List[Dict[str, Union[str, int]]]:
    cached = await redis_client.get(PATIENTS_CACHE_KEY)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return json.loads(cached)

    rows = await db_pool.fetch("SELECT id, name, age FROM patients ORDER BY id")
    patients = [dict(row) for row in rows]
    await redis_client.set(PATIENTS_CACHE_KEY, json.dumps(patients), ex=PATIENTS_CACHE_TTL_SECONDS)
    response.headers["X-Cache"] = "MISS"
    return patients


@app.get("/doctors")
async def get_doctors() -> List[Dict[str, Union[str, int]]]:
    rows = await db_pool.fetch("SELECT id, name, specialty FROM doctors ORDER BY id")
    return [dict(row) for row in rows]


# /metrics and /health are hit constantly by Prometheus and health checks; keep them out of traffic graphs.
Instrumentator(excluded_handlers=["/metrics", "/health"]).instrument(app).expose(app)
