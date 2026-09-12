from fastapi import FastAPI
from app.core.config import config

from app.api import api_router


app = FastAPI()
app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to Converter API!"}

@app.get("/health")
def get_health():
    return {
        "status": "ok",
        "environment": config.dev_env,
    }