from pydantic import BaseModel


class CustomNodeConfig(BaseModel):
    type: str
    name: str
    color: str


class CustomNodesData(BaseModel):
    id: int
    kindName: str
    config: CustomNodeConfig


class CustomNodes(BaseModel):
    data: list[CustomNodesData]
