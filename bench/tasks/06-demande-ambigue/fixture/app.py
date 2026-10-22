"""Petite API de tâches (les données sont perdues à chaque redémarrage)."""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
TASKS = []


class Task(BaseModel):
    title: str
    done: bool = False


@app.get("/tasks")
def list_tasks():
    return TASKS


@app.post("/tasks")
def add_task(task: Task):
    TASKS.append(task.model_dump())
    return {"id": len(TASKS) - 1}
