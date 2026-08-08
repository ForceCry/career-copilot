from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, typed config - previously each module read its own
    os.environ.get()/os.environ[] calls independently (db.py, es_client.py,
    embeddings.py, rabbitmq.py), so a typo'd variable name or a bad value
    (e.g. a non-numeric port) only surfaced deep in a request, in whichever
    module happened to touch it first. Instantiating this once at import
    time makes that fail at startup instead, with a clear pydantic error.

    Adzuna credentials are deliberately NOT here - see AdzunaSettings below.
    """

    model_config = SettingsConfigDict(extra="ignore")

    mysql_user: str = "career_copilot"
    mysql_password: str = "career_copilot"
    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_database: str = "career_copilot"

    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"

    elasticsearch_url: str = "http://elasticsearch:9200"
    embeddings_url: str = "http://embeddings/embed"

    @property
    def mysql_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


class AdzunaSettings(BaseSettings):
    """Split out from Settings on purpose: Adzuna credentials are only
    needed by `scripts/ingest.py --source adzuna`, not by the API/worker -
    making them required fields on the shared Settings object would break
    startup for anyone who hasn't set them but isn't using that source.
    AdzunaSource() instantiates this itself, so it still fails fast, just
    only when that source is actually used."""

    model_config = SettingsConfigDict(env_prefix="ADZUNA_", extra="ignore")

    app_id: str
    app_key: str


settings = Settings()
