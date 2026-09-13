from fastapi import APIRouter

from app.api.example import router as example_router

api_router = APIRouter()
api_router.include_router(example_router)
