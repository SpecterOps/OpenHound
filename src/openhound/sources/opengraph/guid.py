import uuid
from abc import ABC, abstractmethod
from enum import Enum


class Collector(ABC):
    @classmethod
    @abstractmethod
    def guid(cls, name: str, node_type: Enum | str, *args) -> str:
        uuid_namespace = uuid.NAMESPACE_DNS
        type_value = node_type.value if isinstance(node_type, Enum) else node_type
        resource_path = f"{name}.{type_value}.{'.'.join(args)}"
        return str(uuid.uuid5(uuid_namespace, resource_path))
