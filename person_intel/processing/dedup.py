import hashlib
from urllib.parse import urlparse, urlunparse

from person_intel.storage.models import RawDocument


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Drop fragment and trailing slash
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.params,
        parsed.query,
        "",  # no fragment
    ))
    return normalized


def _content_hash(content: str) -> str:
    return hashlib.sha256(content[:500].encode()).hexdigest()


def deduplicate(documents: list[RawDocument], min_length: int = 50) -> list[RawDocument]:
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    result: list[RawDocument] = []

    # Sources that intentionally produce multiple docs with the same URL (batched)
    _BATCHED_SOURCES = {"twitter", "linkedin"}

    for doc in documents:
        if len(doc.content_raw) < min_length:
            continue

        content_hash = _content_hash(doc.content_raw)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        # URL dedup only for web/github — batched sources share a URL by design
        if doc.source not in _BATCHED_SOURCES:
            norm_url = _normalize_url(doc.url)
            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)

        result.append(doc)

    return result
