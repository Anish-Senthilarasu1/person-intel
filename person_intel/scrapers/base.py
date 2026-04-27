from typing import Protocol, runtime_checkable

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument


@runtime_checkable
class BaseScraper(Protocol):
    source_name: str
    requires_credentials: bool

    async def scrape(
        self,
        person: PersonQuery,
        config: Config,
        cache: Cache,
    ) -> list[RawDocument]: ...
