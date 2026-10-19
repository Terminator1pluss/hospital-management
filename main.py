from typing import Dict, List, Union

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


app = FastAPI(title="Hospital Management Application")


@app.get("/patients")
async def get_patients() -> List[Dict[str, Union[str, int]]]:
    return [
        {"id": 1, "name": "Alice Johnson", "age": 34},
        {"id": 2, "name": "Michael Smith", "age": 52},
    ]


@app.get("/doctors")
async def get_doctors() -> List[Dict[str, Union[str, int]]]:
    return [
        {"id": 1, "name": "Dr. Emily Carter", "specialty": "Cardiology"},
        {"id": 2, "name": "Dr. James Wilson", "specialty": "Neurology"},
    ]


Instrumentator().instrument(app).expose(app)