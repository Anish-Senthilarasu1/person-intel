"""
Web scraper — search engine + page fetch.

Search provider priority (uses first configured key):
  1. Serper.dev       — SERPER_API_KEY        (2,500 free on signup, Google results)
  2. Tavily           — TAVILY_API_KEY         (1,000 free/month, research-optimised)
  3. Google CSE       — GOOGLE_CSE_KEY +       (100 free/day)
                        GOOGLE_CSE_CX
  4. Brave Search     — BRAVE_SEARCH_API_KEY   (paid, ~$3/month)
  5. DuckDuckGo       — no key, free scrape,   (fallback, rate-limits aggressively)

Get a free Serper key at https://serper.dev (no credit card).
Get a free Tavily key at https://tavily.com (no credit card).
"""
import asyncio
import random
import time
from datetime import datetime

import httpx
import trafilatura
from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "web"
requires_credentials = False

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# ─── Query templates ──────────────────────────────────────────────────────────

_CORE_TEMPLATES = [
    '"{name}"',                                  # broad — catches everything
    '"{name}" interview',
    '"{name}" biography profile',
    '"{name}" {company}',                        # skipped if no company
    '"{name}" essay blog article',
    '"{name}" podcast talk keynote',
    '"{name}" wikipedia',
    '"{name}" newsletter substack medium',
    '"{name}" news',
]

_HANDLE_TEMPLATES = [
    'site:twitter.com/{twitter} OR site:x.com/{twitter}',
    '"{name}" {twitter}',
]

_LINKEDIN_TEMPLATES = [
    'site:linkedin.com/in/{linkedin}',
]


def _build_queries(person: PersonQuery) -> list[str]:
    queries: list[str] = []
    for tmpl in _CORE_TEMPLATES:
        if "{company}" in tmpl and not person.company:
            continue
        queries.append(tmpl.format(name=person.name, company=person.company or "").strip())

    if person.twitter_handle:
        for tmpl in _HANDLE_TEMPLATES:
            queries.append(tmpl.format(twitter=person.twitter_handle, name=person.name))

    if person.linkedin_slug:
        for tmpl in _LINKEDIN_TEMPLATES:
            queries.append(tmpl.format(linkedin=person.linkedin_slug))

    return queries


# ─── Search providers ─────────────────────────────────────────────────────────

async def _serper_search(query: str, api_key: str, n: int = 10) -> list[tuple[str, str]]:
    """Serper.dev — real Google results. 2,500 free searches on signup."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": n},
            )
            if resp.status_code != 200:
                logger.debug(f"Serper: {resp.status_code} for '{query}'")
                return []
            data = resp.json()
            results = []
            for r in data.get("organic", []):
                url = r.get("link", "")
                snippet = r.get("snippet", "")
                if url:
                    results.append((url, snippet))
            # Also pull knowledge graph snippet if present
            kg = data.get("knowledgeGraph", {})
            if kg.get("descriptionLink"):
                results.insert(0, (kg["descriptionLink"], kg.get("description", "")))
            return results
    except Exception as e:
        logger.debug(f"Serper search failed: {e}")
        return []


async def _tavily_search(query: str, api_key: str, n: int = 10) -> list[tuple[str, str]]:
    """Tavily — research-optimised search. 1,000 free searches/month."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": n,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            if resp.status_code != 200:
                logger.debug(f"Tavily: {resp.status_code} for '{query}'")
                return []
            data = resp.json()
            results = []
            for r in data.get("results", []):
                url = r.get("url", "")
                snippet = r.get("content", "") or r.get("snippet", "")
                if url:
                    results.append((url, snippet))
            return results
    except Exception as e:
        logger.debug(f"Tavily search failed: {e}")
        return []


async def _google_cse_search(
    query: str, api_key: str, cx: str, n: int = 10
) -> list[tuple[str, str]]:
    """Google Programmable Search Engine — 100 free queries/day."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cx, "q": query, "num": min(n, 10)},
            )
            if resp.status_code != 200:
                logger.debug(f"Google CSE: {resp.status_code} for '{query}'")
                return []
            data = resp.json()
            results = []
            for item in data.get("items", []):
                url = item.get("link", "")
                snippet = item.get("snippet", "")
                if url:
                    results.append((url, snippet))
            return results
    except Exception as e:
        logger.debug(f"Google CSE search failed: {e}")
        return []


async def _brave_search(query: str, api_key: str, n: int = 10) -> list[tuple[str, str]]:
    """Brave Search API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                params={"q": query, "count": n, "text_decorations": False},
            )
            if resp.status_code != 200:
                logger.debug(f"Brave: {resp.status_code} for '{query}'")
                return []
            data = resp.json()
            results = []
            for r in data.get("web", {}).get("results", []):
                url = r.get("url", "")
                snippet = r.get("description", "") or r.get("snippet", "")
                if url:
                    results.append((url, snippet))
            return results
    except Exception as e:
        logger.debug(f"Brave search failed: {e}")
        return []


def _ddg_search_with_retry(query: str, max_results: int = 8, max_attempts: int = 4) -> list[dict]:
    """DuckDuckGo fallback — free but aggressively rate-limited."""
    from duckduckgo_search import DDGS

    delay = 5.0
    for attempt in range(max_attempts):
        try:
            headers = {"User-Agent": random.choice(_USER_AGENTS)}
            with DDGS(headers=headers) as ddgs:
                results = ddgs.text(query, max_results=max_results)
                return results or []
        except Exception as e:
            err_str = str(e).lower()
            is_ratelimit = any(x in err_str for x in ("ratelimit", "202", "rate", "429"))
            if attempt < max_attempts - 1:
                sleep_time = max(delay + random.uniform(1, 4), 15.0 if is_ratelimit else 0)
                if is_ratelimit:
                    logger.warning(f"DDG rate-limited — sleeping {sleep_time:.1f}s (attempt {attempt+1}/{max_attempts})")
                else:
                    logger.debug(f"DDG error: {e} — retrying in {sleep_time:.1f}s")
                time.sleep(sleep_time)
                delay *= 2
            else:
                logger.warning(f"DDG: gave up after {max_attempts} attempts for '{query[:60]}…'")
                return []
    return []


async def _search(query: str, config: Config) -> list[tuple[str, str]]:
    """
    Run query through the first available search provider.
    Returns list of (url, snippet).
    """
    serper_key = getattr(config, "serper_api_key", None)
    tavily_key = getattr(config, "tavily_api_key", None)
    google_key = getattr(config, "google_cse_key", None)
    google_cx  = getattr(config, "google_cse_cx", None)
    brave_key  = getattr(config, "brave_search_api_key", None)

    if serper_key:
        return await _serper_search(query, serper_key, n=10)

    if tavily_key:
        return await _tavily_search(query, tavily_key, n=10)

    if google_key and google_cx:
        return await _google_cse_search(query, google_key, google_cx, n=10)

    if brave_key:
        return await _brave_search(query, brave_key, n=10)

    # DDG fallback — run in executor so async event loop isn't blocked
    loop = asyncio.get_event_loop()
    ddg_results = await loop.run_in_executor(None, _ddg_search_with_retry, query, 8, 4)
    results = []
    for r in ddg_results:
        url = r.get("href", "")
        snippet = r.get("body", "")
        if url:
            results.append((url, snippet))
    return results


def _active_provider(config: Config) -> str:
    if getattr(config, "serper_api_key", None):
        return "serper"
    if getattr(config, "tavily_api_key", None):
        return "tavily"
    if getattr(config, "google_cse_key", None) and getattr(config, "google_cse_cx", None):
        return "google_cse"
    if getattr(config, "brave_search_api_key", None):
        return "brave"
    return "ddg"


# ─── URL fetch ────────────────────────────────────────────────────────────────

async def _fetch_url(url: str, cache: Cache) -> str | None:
    cached = cache.get(url)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": random.choice(_USER_AGENTS)},
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                text = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
                if text and len(text) > 200:
                    cache.set(url, text)
                    return text
    except Exception as e:
        logger.debug(f"httpx failed for {url}: {e}")

    try:
        html = trafilatura.fetch_url(url)
        if html:
            text = trafilatura.extract(html, include_comments=False, include_tables=False)
            if text and len(text) > 200:
                cache.set(url, text)
                return text
    except Exception as e:
        logger.debug(f"trafilatura failed for {url}: {e}")

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=random.choice(_USER_AGENTS),
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            html = await page.content()
            await browser.close()
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 200:
            cache.set(url, text)
            return text
    except Exception as e:
        logger.debug(f"Playwright failed for {url}: {e}")

    return None


# ─── Entry point ──────────────────────────────────────────────────────────────

async def scrape(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    queries = _build_queries(person)
    provider = _active_provider(config)
    logger.info(f"Web: {len(queries)} queries for '{person.name}' via {provider}")

    name_parts_list = [p.lower() for p in person.name.split() if len(p) > 2]
    handle_terms = set()
    for attr in ("twitter_handle", "linkedin_slug", "github_username"):
        val = getattr(person, attr, None)
        if val:
            handle_terms.add(val.lower().lstrip("@"))

    def _is_relevant(url: str, snippet: str) -> bool:
        combined = (url + " " + snippet).lower()
        if any(term in combined for term in handle_terms if len(term) > 3):
            return True
        if name_parts_list and all(part in combined for part in name_parts_list):
            return True
        return False

    discovered: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for query in queries:
        results = await _search(query, config)

        for url, snippet in results:
            if not url or url in seen_urls:
                continue
            if not _is_relevant(url, snippet):
                logger.debug(f"Web: irrelevant — {url[:80]}")
                continue
            seen_urls.add(url)
            discovered.append((url, snippet))
            logger.debug(f"Web: accepted — {url[:80]}")

        # Throttle between queries — longer delay for DDG, short for real APIs
        if provider == "ddg":
            await asyncio.sleep(random.uniform(5.0, 9.0))
        else:
            await asyncio.sleep(random.uniform(0.2, 0.5))

    logger.info(f"Web: {len(discovered)} relevant URLs from {len(queries)} queries")

    to_fetch = discovered[: config.max_web_pages]
    documents: list[RawDocument] = []

    for url, snippet in to_fetch:
        await asyncio.sleep(random.uniform(0.8, 2.0))
        text = await _fetch_url(url, cache)
        if text:
            documents.append(
                RawDocument(
                    source="web",
                    url=url,
                    content_raw=text,
                    metadata={"snippet": snippet, "search_provider": provider},
                    fetched_at=datetime.utcnow(),
                )
            )
            logger.debug(f"Web: fetched {len(text)} chars — {url[:70]}")
        else:
            logger.debug(f"Web: no content — {url[:70]}")

    logger.info(f"Web scraper: {len(documents)} pages from {len(to_fetch)} URLs")
    return documents
