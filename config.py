import os


class Settings:
    BANK_NAME: str = os.getenv("BANK_NAME", "My Branch Bank")
    BANK_ADDRESS: str = os.getenv("BANK_ADDRESS", "http://localhost:8000")
    CENTRAL_BANK_URL: str = os.getenv("CENTRAL_BANK_URL", "https://test.diarainfra.com/central-bank/api/v1")
    
    PRIVATE_KEY_PATH: str = os.getenv("PRIVATE_KEY_PATH", "keys/private_key.pem")
    PUBLIC_KEY_PATH: str = os.getenv("PUBLIC_KEY_PATH", "keys/public_key.pem")
    
    HEARTBEAT_INTERVAL_MINUTES: int = int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "25"))
    
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./bank.db")


settings = Settings()
