from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    BANK_NAME: str = "My Branch Bank"
    BANK_ADDRESS: str = "http://localhost:8000"
    CENTRAL_BANK_URL: str = "https://test.diarainfra.com/central-bank/api/v1"
    
    PRIVATE_KEY_PATH: str = "keys/private_key.pem"
    PUBLIC_KEY_PATH: str = "keys/public_key.pem"
    
    HEARTBEAT_INTERVAL_MINUTES: int = 25
    
    DATABASE_URL: str = "sqlite:///./bank.db"
    
    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
