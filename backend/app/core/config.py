from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    app_name: str = "Playlist Converter"
    dev_env: str = "dev"
    frontend_url: str = "http://localhost:5173"
    database_url: str = "sqlite:///./playlist.db"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    secret_key: str = "local-development-only-change-before-deploying"
    token_encryption_key: str = ""
    cookie_secure: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/youtube/callback"
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8000/auth/spotify/callback"
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: str = ""
    demo_mode: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


config = Config()
