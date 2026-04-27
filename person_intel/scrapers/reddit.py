"""
Reddit scraper — old.reddit.com JSON API (100% free, no key required).

Finds:
- Reddit posts mentioning the person
- Subreddit threads (AMAs, discussions)
- Comments (via post thread fetching)

Uses old.reddit.com which returns JSON without requiring OAuth.
"""
import asyncio
import random
import re
from datetime import datetime

import httpx
from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "reddit"
requires_credentials = False

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-autobio/1.0; personal research tool)"
}

_RELEVANT_SUBREDDITS = [
    "startups",
    "entrepreneur",
    "technology",
    "programming",
    "MachineLearning",
    "artificial",
    "cscareerquestions",
    "IAmA",
    "business",
    "investing",
    "venturecapital",
    "YCombinator",
    "SiliconValleyHBO",
]


async def _reddit_search(
    query: str,
    subreddit: str | None = None,
    sort: str = "relevance",
    n: int = 10,
) -> list[dict]:
    """Search Reddit posts. Returns list of post data dicts."""
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {"q": query, "restrict_sr": "1", "sort": sort, "limit": n}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": query, "sort": sort, "limit": n, "type": "link"}

    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                logger.warning("Reddit: rate limited, sleeping 10s")
                await asyncio.sleep(10)
                return []
            if resp.status_code != 200:
                logger.debug(f"Reddit search: {resp.status_code} for '{query}'")
                return []
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            return [p["data"] for p in posts if p.get("data")]
    except Exception as e:
        logger.debug(f"Reddit search failed: {e}")
        return []


async def _fetch_post_comments(post_id: str, subreddit: str, max_comments: int = 10) -> str:
    """Fetch top comments from a Reddit post thread."""
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url, params={"limit": max_comments, "depth": 2})
            if resp.status_code != 200:
                return ""
            data = resp.json()
            if not isinstance(data, list) or len(data) < 2:
                return ""
            comments_data = data[1].get("data", {}).get("children", [])
            texts = []
            for item in comments_data:
                body = item.get("data", {}).get("body", "")
                if body and body != "[deleted]" and body != "[removed]" and len(body) > 20:
                    texts.append(body)
            return "\n\n---\n\n".join(texts[:max_comments])
    except Exception as e:
        logger.debug(f"Reddit comments failed for {post_id}: {e}")
        return ""


def _post_to_text(post: dict, comments: str = "") -> str:
    parts = []
    title = post.get("title", "")
    if title:
        parts.append(f"Title: {title}")
    selftext = post.get("selftext", "")
    if selftext and selftext not in ("[removed]", "[deleted]"):
        parts.append(selftext[:2000])
    if comments:
        parts.append(f"Top comments:\n{comments[:1500]}")
    author = post.get("author", "")
    if author:
        parts.append(f"Author: u/{author}")
    score = post.get("score")
    if score:
        parts.append(f"Score: {score}")
    num_comments = post.get("num_comments")
    if num_comments:
        parts.append(f"Comments: {num_comments}")
    sub = post.get("subreddit", "")
    if sub:
        parts.append(f"Subreddit: r/{sub}")
    return "\n".join(parts)


async def scrape(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    name = person.name
    name_parts = [p.lower() for p in name.split() if len(p) > 2]
    documents: list[RawDocument] = []
    seen_ids: set[str] = set()

    # Build search queries
    queries = [f'"{name}"']
    if person.company:
        queries.append(f'"{name}" {person.company}')
    if person.twitter_handle:
        queries.append(f'"{name}" {person.twitter_handle}')

    logger.info(f"Reddit: searching for '{name}'")

    all_posts: list[dict] = []

    # Global Reddit search
    for query in queries:
        posts = await _reddit_search(query, sort="relevance", n=10)
        all_posts.extend(posts)
        await asyncio.sleep(random.uniform(1.5, 3.0))

    # Search r/IAmA specifically for AMA posts
    ama_posts = await _reddit_search(f'"{name}" AMA', subreddit="IAmA", n=5)
    all_posts.extend(ama_posts)
    await asyncio.sleep(random.uniform(1.5, 2.5))

    # Deduplicate and filter relevant posts
    relevant_posts: list[dict] = []
    for post in all_posts:
        post_id = post.get("id", "")
        if not post_id or post_id in seen_ids:
            continue

        combined = (
            (post.get("title") or "")
            + " "
            + (post.get("selftext") or "")
            + " "
            + (post.get("url") or "")
        ).lower()

        if not all(part in combined for part in name_parts):
            continue

        seen_ids.add(post_id)
        relevant_posts.append(post)

    logger.info(f"Reddit: {len(relevant_posts)} relevant posts found")

    # Fetch comments for top posts (by score), cap at 8 posts
    top_posts = sorted(relevant_posts, key=lambda p: p.get("score", 0), reverse=True)[:8]

    for post in top_posts:
        post_id = post.get("id", "")
        subreddit = post.get("subreddit", "")
        permalink = post.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink else f"https://www.reddit.com/comments/{post_id}"

        comments = ""
        num_comments = post.get("num_comments", 0)
        if num_comments > 0 and subreddit:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            comments = await _fetch_post_comments(post_id, subreddit, max_comments=8)

        text = _post_to_text(post, comments)
        if not text or len(text) < 30:
            continue

        documents.append(
            RawDocument(
                source="reddit",
                url=url,
                content_raw=text,
                metadata={
                    "score": post.get("score"),
                    "subreddit": subreddit,
                    "author": post.get("author"),
                    "num_comments": num_comments,
                    "title": post.get("title"),
                },
                fetched_at=datetime.utcnow(),
            )
        )

    logger.info(f"Reddit scraper: {len(documents)} posts with content")
    return documents
