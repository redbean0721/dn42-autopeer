from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REGISTRY_")

    repo_url: str = "https://git.dn42.dev/dn42/registry.git"
    branch: str = "master"
    workdir: Path = Path("/data/registry")
    git_username: str = Field(min_length=1)
    git_token: SecretStr = Field(min_length=1)
    git_timeout: float = Field(300, gt=0)

    database_url: SecretStr = SecretStr("")
    reader_roles: list[str] = ["autopeer"]

    sync_interval: float = Field(300, ge=30)
    retry_interval: float = Field(60, ge=5)
    trigger_cooldown: float = Field(30, ge=0)
    min_objects: int = Field(1000, ge=0)
    gc_every: int = Field(50, ge=1)

    http_host: str = "0.0.0.0"
    http_port: int = 8080
    log_level: str = "INFO"
