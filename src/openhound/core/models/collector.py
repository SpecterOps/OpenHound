from pydantic import BaseModel


class Collector(BaseModel):
    name: str
    homepage: str
    repo: str
    description: str
    # last_updated: str
    version: str
    authors: list[str] | str
    license: str
    logo: str
    # tagline: str
    tags: list[str]
    category: str
    provider: str
    platform: str
