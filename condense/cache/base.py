from abc import ABC, abstractmethod
from typing import Optional


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[dict]:
        pass

    @abstractmethod
    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass

    @abstractmethod
    async def size(self) -> int:
        pass

    @abstractmethod
    async def clear(self) -> None:
        pass
