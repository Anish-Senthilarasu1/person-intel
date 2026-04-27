"""
Hacker News scraper — Algolia HN Search API (100% free, no key required).

Finds:
- Stories submitted by the person (by HN username if known)
- Comments mentioning the person by name
- Any HN threads where they are discussed
"""
import asyncio
from datetime import datetime

import httpx
from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "hackernews"
requires_credentials = False

_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


async def _hn_search(query: str, tags: str = "story", n: int = 20) -> list[dict]:
    """Search HN via Algolia API. tags can be 'story', 'comment', 'story,author_<user>'."""
    url = f"{_ALGOLIA_BASE}/search"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                params={"query": query, "tags": tags, "hitsPerPage": n},
            )
            if resp.status_code != 200:
                logger.debug(f"HN Algolia: {resp.status_code} for '{query}'")
                return []
            return resp.json().get("hits", [])
    except Exception as e:
        logger.debug(f"HN search failed: {e}")
        return []


async def _hn_user_stories(hn_username: str, n: int = 30) -> list[dict]:
    """Fetch stories submitted by a specific HN user."""
    url = f"{_ALGOLIA_BASE}/search"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                params={
                    "tags": f"story,author_{hn_username}",
                    "hitsPerPage": n,
                },
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("hits", [])
    except Exception as e:
        logger.debug(f"HN user stories failed: {e}")
        return []


async def _hn_user_comments(hn_username: str, n: int = 30) -> list[dict]:
    """Fetch comments submitted by a specific HN user."""
    url = f"{_ALGOLIA_BASE}/search"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                params={
                    "tags": f"comment,author_{hn_username}",
                    "hitsPerPage": n,
                },
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("hits", [])
    except Exception as e:
        logger.debug(f"HN user comments failed: {e}")
        return []


def _hit_to_text(hit: dict) -> str:
    parts = []
    title = hit.get("title") or hit.get("story_title", "")
    if title:
        parts.append(f"Title: {title}")
    text = hit.get("story_text") or hit.get("comment_text") or ""
    if text:
        # Strip basic HTML tags
        import re
        text = re.sub(r"<[^>]+>", " ", text).strip()
        parts.append(text)
    url = hit.get("url", "")
    if url:
        parts.append(f"URL: {url}")
    author = hit.get("author", "")
    if author:
        parts.append(f"Author: {author}")
    points = hit.get("points")
    if points:
        parts.append(f"Points: {points}")
    num_comments = hit.get("num_comments")
    if num_comments:
        parts.append(f"Comments: {num_comments}")
    return "\n".join(parts)


def _hit_url(hit: dict) -> str:
    obj_id = hit.get("objectID", "")
    return f"https://news.ycombinator.com/item?id={obj_id}"


async def scrape(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    name = person.name
    name_parts = [p.lower() for p in name.split() if len(p) > 2]
    documents: list[RawDocument] = []
    seen_ids: set[str] = set()

    queries = [
        f'"{name}"',
        f"{name} founder",
        f"{name} startup",
    ]
    if person.company:
        queries.append(f'"{name}" {person.company}')

    logger.info(f"HN: searching for '{name}' ({len(queries)} queries)")

    all_hits: list[dict] = []

    # Search stories mentioning the person
    for query in queries:
        hits = await _hn_search(query, tags="story", n=15)
        all_hits.extend(hits)
        await asyncio.sleep(0.3)

    # Search comments mentioning the person
    hits = await _hn_search(f'"{name}"', tags="comment", n=20)
    all_hits.extend(hits)
    await asyncio.sleep(0.3)

    # If twitter handle might double as HN username, try fetching their stories
    if person.twitter_handle:
        hn_user = person.twitter_handle.lstrip("@").lower()
        user_stories = await _hn_user_stories(hn_username=hn_user, n=20)
        if user_stories:
            logger.info(f"HN: found {len(user_stories)} stories for user '{hn_user}'")
            all_hits.extend(user_stories)
        user_comments = await _hn_user_comments(hn_username=hn_user, n=20)
        if user_comments:
            logger.info(f"HN: found {len(user_comments)} comments for user '{hn_user}'")
            all_hits.extend(user_comments)
        await asyncio.sleep(0.3)

    for hit in all_hits:
        obj_id = hit.get("objectID", "")
        if not obj_id or obj_id in seen_ids:
            continue

        # Relevance: name parts must appear somewhere in the hit content
        combined = (
            (hit.get("title") or "")
            + " "
            + (hit.get("story_text") or "")
            + " "
            + (hit.get("comment_text") or "")
            + " "
            + (hit.get("author") or "")
        ).lower()

        if not all(part in combined for part in name_parts):
            continue

        text = _hit_to_text(hit)
        if not text or len(text) < 30:
            continue

        seen_ids.add(obj_id)
        hn_url = _hit_url(hit)
        created_at_str = hit.get("created_at", "")

        documents.append(
            RawDocument(
                source="hackernews",
                url=hn_url,
                content_raw=text,
                metadata={
                    "points": hit.get("points"),
                    "author": hit.get("author"),
                    "created_at": created_at_str,
                    "type": "story" if hit.get("title") else "comment",
                },
                fetched_at=datetime.utcnow(),
            )
        )

    logger.info(f"HN scraper: {len(documents)} relevant items found")
    return documents
