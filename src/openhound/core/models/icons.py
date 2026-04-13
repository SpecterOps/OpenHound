from pydantic import BaseModel


class CustomNodeIcon(BaseModel):
    type: str
    name: str
    color: str


class CustomNodeType(BaseModel):
    icon: CustomNodeIcon


class CustomNode(BaseModel):
    custom_types: dict[str, CustomNodeType]
