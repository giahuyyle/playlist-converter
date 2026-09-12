from dotenv import load_dotenv
import os

from pydantic_settings import BaseSettings

load_dotenv()

class Config(BaseSettings):
    app_name: str = "Playlist Converter"
    dev_env: str = (
        "development"
        if os.getenv("DEV_ENV", "dev").lower() == "dev"
        else "production"
    )

    frontend_url: str = os.getenv("FRONTENT_URL")


config = Config()