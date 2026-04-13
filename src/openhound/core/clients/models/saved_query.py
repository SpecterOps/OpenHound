from datetime import datetime

from pydantic import BaseModel


class SavedQueryData(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    user_id: str
    name: str
    query: str
    description: str | None = None


class SavedQuery(BaseModel):
    data: SavedQueryData


class SavedSearchResponse(BaseModel):
    id: int
    name: str
    query: str


class SavedQueries(BaseModel):
    count: int
    data: list[SavedSearchResponse]
