from pydantic import BaseModel


class Tag(BaseModel):
    id: int
    type: int
    kind_id: int
    name: str
    description: str


class AssetGroupTag(BaseModel):
    tags: list[Tag]


class AssetGroupsTags(BaseModel):
    data: AssetGroupTag


class Selector(BaseModel):
    id: int
    asset_group_tag_id: int
    name: str


class SelectorsData(BaseModel):
    selectors: list[Selector]


class Selectors(BaseModel):
    data: SelectorsData
