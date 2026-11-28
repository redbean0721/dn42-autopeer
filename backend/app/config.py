from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: SecretStr = SecretStr("postgresql+psycopg://")
    database_connect_timeout: int = 3
