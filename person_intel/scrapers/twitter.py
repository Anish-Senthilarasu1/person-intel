"""
Twitter / X scraper.

Priority order:
  1. socialdata.tools — ~$0.05/1K tweets, cleanest API
  2. TwitterAPI.io    — ~$0.15/1K tweets
  3. Optional non-API fallbacks only if twitter_api_only=False
"""
import asyncio
import json
import random
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "twitter"
requires_credentials = False

# X's web app bearer token — public, stable across clients
_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I62BeUz4fUo%3D"
    "EUifiRBkKG5E2XYMLgk93IAaeXqKqbBB2B%2BpuxuVPRA2Q"
)

# Where we store the user-provided cookies
_COOKIES_FILE = Path("./data/x_cookies.json")


def _x_headers(auth_token: str, ct0: str) -> dict:
    return {
        "authorization": f"Bearer {_X_BEARER}",
        "x-csrf-token": ct0,
        "cookie": f"auth_token={auth_token}; ct0={ct0}",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "x-twitter-active-user": "yes",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }


def load_x_cookies() -> tuple[str, str] | None:
    """Return (auth_token, ct0) from saved cookie file, or None."""
    try:
        if _COOKIES_FILE.exists():
            data = json.loads(_COOKIES_FILE.read_text())
            return data["auth_token"], data["ct0"]
    except Exception:
        pass
    return None


def save_x_cookies(auth_token: str, ct0: str) -> None:
    _COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_text(json.dumps({"auth_token": auth_token, "ct0": ct0}))


async def _scrape_via_x_graphql(
    handle: str, max_tweets: int, auth_token: str, ct0: str, cache: Cache
) -> list[RawDocument]:
    """
    Scrape tweets using X's internal GraphQL API with browser cookies.
    Same endpoints the x.com web app uses — no third-party library needed.
    """
    cache_key = f"x_graphql_{handle}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"X GraphQL: cache hit for @{handle}")
        return [RawDocument(
            source="twitter", url=f"https://x.com/{handle}",
            content_raw=cached,
            metadata={"handle": handle, "via": "x_graphql_cached"},
            fetched_at=datetime.utcnow(),
        )]

    headers = _x_headers(auth_token, ct0)

    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        # Step 1: resolve handle → user_id
        user_id = None
        try:
            variables = json.dumps({"screen_name": handle, "withSafetyModeUserFields": True})
            features = json.dumps({
                "hidden_profile_likes_enabled": True,
                "hidden_profile_subscriptions_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "subscriptions_verification_info_is_identity_verified_enabled": True,
                "subscriptions_verification_info_verified_since_enabled": True,
                "highlights_tweets_tab_ui_enabled": True,
                "responsive_web_twitter_article_notes_tab_enabled": False,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
            })
            r = await client.get(
                "https://twitter.com/i/api/graphql/G3KGOASz96M-Qu0nwmGXNg/UserByScreenName",
                params={"variables": variables, "features": features},
            )
            data = r.json()
            user_id = data["data"]["user"]["result"]["rest_id"]
            logger.info(f"X GraphQL: resolved @{handle} → user_id {user_id}")
        except Exception as e:
            logger.warning(f"X GraphQL: failed to resolve user_id for @{handle}: {e}")
            return []

        # Step 2: paginate UserTweets
        tweet_parts: list[str] = []
        cursor = None
        pages = 0
        max_pages = (max_tweets // 20) + 3

        while len(tweet_parts) < max_tweets and pages < max_pages:
            variables_d: dict = {
                "userId": user_id,
                "count": 20,
                "includePromotedContent": False,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            }
            if cursor:
                variables_d["cursor"] = cursor

            features_tweets = json.dumps({
                "rweb_lists_timeline_redesign_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "tweetypie_unmention_optimization_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": False,
                "tweet_awards_web_tipping_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_media_download_video_enabled": False,
                "responsive_web_enhance_cards_enabled": False,
            })

            try:
                r = await client.get(
                    "https://twitter.com/i/api/graphql/H8OOoI-5ZE4NxgRr8lfyWg/UserTweets",
                    params={"variables": json.dumps(variables_d), "features": features_tweets},
                )
                if r.status_code == 429:
                    logger.warning("X GraphQL: rate limited, stopping pagination")
                    break
                if r.status_code != 200:
                    logger.warning(f"X GraphQL: status {r.status_code} on page {pages}")
                    break

                body = r.json()
                instructions = (
                    body.get("data", {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline_v2", {})
                    .get("timeline", {})
                    .get("instructions", [])
                )

                new_tweets = 0
                cursor = None
                for instr in instructions:
                    for entry in instr.get("entries", []):
                        entry_id = entry.get("entryId", "")
                        content = entry.get("content", {})

                        # Cursor for next page
                        if "cursor-bottom" in entry_id:
                            cursor = content.get("value")
                            continue

                        # Tweet entry
                        item_content = content.get("itemContent", {})
                        tweet_result = item_content.get("tweet_results", {}).get("result", {})
                        if tweet_result.get("__typename") == "TweetWithVisibilityResults":
                            tweet_result = tweet_result.get("tweet", tweet_result)
                        legacy = tweet_result.get("legacy", {})
                        text = legacy.get("full_text", "")
                        created = legacy.get("created_at", "")
                        if not text or text.startswith("RT "):
                            continue
                        try:
                            dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
                            tweet_parts.append(f"[{dt.strftime('%Y-%m-%d')}] {text}")
                        except Exception:
                            tweet_parts.append(text)
                        new_tweets += 1

                logger.debug(f"X GraphQL: page {pages+1}, +{new_tweets} tweets ({len(tweet_parts)} total)")
                pages += 1

                if not cursor or new_tweets == 0:
                    break

                await asyncio.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                logger.warning(f"X GraphQL: page {pages} error: {e}")
                break

    if not tweet_parts:
        logger.warning(f"X GraphQL: 0 tweets for @{handle}")
        return []

    cache.set(cache_key, "\n".join(tweet_parts))
    logger.info(f"X GraphQL: {len(tweet_parts)} tweets for @{handle}")

    docs = []
    for i in range(0, len(tweet_parts), 50):
        batch = tweet_parts[i: i + 50]
        docs.append(RawDocument(
            source="twitter", url=f"https://x.com/{handle}",
            content_raw="\n".join(batch),
            metadata={"handle": handle, "via": "x_graphql", "batch": i // 50, "tweet_count": len(batch)},
            fetched_at=datetime.utcnow(),
        ))
    return docs


# ──────────────────────────────────────────────────────────────────────────────
# 1. socialdata.tools  (~$0.05/1K tweets, cleanest API)
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_via_socialdata(
    handle: str, max_tweets: int, api_key: str, cache: Cache
) -> list[RawDocument]:
    cache_key = f"socialdata_{handle}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"socialdata: cache hit for @{handle}")
        return [RawDocument(
            source="twitter", url=f"https://x.com/{handle}",
            content_raw=cached,
            metadata={"handle": handle, "via": "socialdata_cached"},
            fetched_at=datetime.utcnow(),
        )]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    tweet_parts: list[str] = []
    cursor = None

    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        # Resolve handle → user_id
        try:
            r = await client.get(f"https://api.socialdata.tools/twitter/user/{handle}")
            if r.status_code != 200:
                logger.warning(f"socialdata: user lookup failed {r.status_code}: {r.text[:200]}")
                return []
            user_id = r.json()["id_str"]
            logger.info(f"socialdata: @{handle} → id {user_id}")
        except Exception as e:
            logger.warning(f"socialdata: user lookup error: {e}")
            return []

        # Paginate tweets
        pages = 0
        max_pages = (max_tweets // 20) + 3
        while len(tweet_parts) < max_tweets and pages < max_pages:
            params: dict = {}
            if cursor:
                params["cursor"] = cursor

            try:
                r = await client.get(
                    f"https://api.socialdata.tools/twitter/user/{user_id}/tweets",
                    params=params,
                )
                if r.status_code == 429:
                    logger.warning("socialdata: rate limited")
                    break
                if r.status_code != 200:
                    logger.warning(f"socialdata: tweets status {r.status_code}")
                    break

                data = r.json()
                tweets = data.get("tweets", [])
                for t in tweets:
                    text = t.get("full_text") or t.get("text") or ""
                    if not text or text.startswith("RT "):
                        continue
                    created = t.get("tweet_created_at") or t.get("created_at") or ""
                    try:
                        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
                        tweet_parts.append(f"[{dt.strftime('%Y-%m-%d')}] {text}")
                    except Exception:
                        tweet_parts.append(text)

                cursor = data.get("next_cursor")
                pages += 1
                logger.debug(f"socialdata: page {pages}, {len(tweet_parts)} tweets")

                if not cursor or not tweets:
                    break

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning(f"socialdata: page {pages} error: {e}")
                break

    if not tweet_parts:
        logger.warning(f"socialdata: 0 tweets for @{handle}")
        return []

    cache.set(cache_key, "\n".join(tweet_parts))
    logger.info(f"socialdata: {len(tweet_parts)} tweets for @{handle}")

    docs = []
    for i in range(0, len(tweet_parts), 50):
        batch = tweet_parts[i: i + 50]
        docs.append(RawDocument(
            source="twitter", url=f"https://x.com/{handle}",
            content_raw="\n".join(batch),
            metadata={"handle": handle, "via": "socialdata", "batch": i // 50, "tweet_count": len(batch)},
            fetched_at=datetime.utcnow(),
        ))
    return docs


# ──────────────────────────────────────────────────────────────────────────────
# 2. TwitterAPI.io
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_via_twitterapi_io(
    handle: str, max_tweets: int, api_key: str, cache: Cache
) -> list[RawDocument]:
    """
    Fetch up to max_tweets tweets via twitterapi.io.
    Uses /twitter/user/last_tweets (userName-based, no userId lookup needed).
    Response structure: d["data"]["tweets"], paginated via next_cursor.
    Free tier: 1 req / 5s.
    """
    cache_key = f"twitterapiio_{handle}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"TwitterAPI.io: cache hit for @{handle}")
        return [RawDocument(
            source="twitter",
            url=f"https://x.com/{handle}",
            content_raw=cached,
            metadata={"handle": handle, "via": "twitterapi.io_cached"},
            fetched_at=datetime.utcnow(),
        )]

    base = "https://api.twitterapi.io"
    headers = {"X-API-Key": api_key}
    tweet_parts: list[str] = []
    cursor = ""
    max_pages = (max_tweets // 20) + 2

    async with httpx.AsyncClient(timeout=20) as client:
        for page in range(max_pages):
            if len(tweet_parts) >= max_tweets:
                break

            params: dict = {"userName": handle}
            if cursor:
                params["cursor"] = cursor

            try:
                r = await client.get(
                    f"{base}/twitter/user/last_tweets",
                    headers=headers,
                    params=params,
                )
            except Exception as e:
                logger.warning(f"TwitterAPI.io: request error on page {page}: {e}")
                break

            if r.status_code == 429:
                logger.warning("TwitterAPI.io: rate limited (429) — waiting 10s")
                await asyncio.sleep(10)
                continue
            if r.status_code != 200:
                logger.warning(f"TwitterAPI.io: error {r.status_code}: {r.text[:200]}")
                break

            data = r.json()
            # Tweets are always nested at data["data"]["tweets"]
            tweets = data.get("data", {}).get("tweets") or []

            for t in tweets:
                text = t.get("text") or ""
                created = t.get("createdAt") or ""
                if not text or len(text) < 5:
                    continue
                if text.startswith("RT "):
                    continue  # skip retweets; they'll show in source's profile anyway
                try:
                    dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
                    tweet_parts.append(f"[{dt.strftime('%Y-%m-%d')}] {text}")
                except Exception:
                    tweet_parts.append(text)

            has_next = data.get("has_next_page", False)
            cursor = data.get("next_cursor", "")
            logger.debug(f"TwitterAPI.io: page {page + 1}, {len(tweet_parts)} tweets so far")

            if not has_next or not cursor:
                break

            await asyncio.sleep(5.5)  # free tier: 1 req / 5s

    if not tweet_parts:
        logger.warning(f"TwitterAPI.io: 0 original tweets for @{handle}")
        return []

    cache.set(cache_key, "\n".join(tweet_parts))
    logger.info(f"TwitterAPI.io: {len(tweet_parts)} tweets for @{handle}")

    docs = []
    for i in range(0, len(tweet_parts), 50):
        batch = tweet_parts[i : i + 50]
        docs.append(RawDocument(
            source="twitter",
            url=f"https://x.com/{handle}",
            content_raw="\n".join(batch),
            metadata={"handle": handle, "via": "twitterapi.io", "batch": i // 50, "tweet_count": len(batch)},
            fetched_at=datetime.utcnow(),
        ))
    return docs


# ──────────────────────────────────────────────────────────────────────────────
# 2. twscrape  (cookie-based)
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_via_twscrape(person: PersonQuery, config: Config, cache: Cache) -> list[RawDocument]:
    import twscrape
    handle = person.twitter_handle
    documents: list[RawDocument] = []

    cache_key = f"twscrape_{handle or person.name}"
    cached = cache.get(cache_key)
    if cached:
        return [RawDocument(**d) for d in cached]

    api = twscrape.API(str(config.twscrape_accounts_db))

    if handle:
        user = await api.user_by_login(handle)
        if user:
            tweet_parts: list[str] = []
            async for tweet in api.user_tweets_and_replies(user.id, limit=config.max_tweets):
                tweet_parts.append(f"[{tweet.date.strftime('%Y-%m-%d')}] {tweet.rawContent}")
                if len(tweet_parts) % 50 == 0:
                    await asyncio.sleep(random.uniform(1.5, 3.5))

            for i in range(0, len(tweet_parts), 50):
                batch = tweet_parts[i : i + 50]
                documents.append(RawDocument(
                    source="twitter", url=f"https://x.com/{handle}",
                    content_raw="\n".join(batch),
                    metadata={"handle": handle, "batch": i // 50, "tweet_count": len(batch)},
                    fetched_at=datetime.utcnow(),
                ))

    # Mentions search
    mention_parts: list[str] = []
    async for tweet in api.search(f'"{person.name}"', limit=100):
        mention_parts.append(
            f"[{tweet.date.strftime('%Y-%m-%d')}] @{tweet.user.username}: {tweet.rawContent}"
        )
        if len(mention_parts) % 50 == 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))

    if mention_parts:
        for i in range(0, len(mention_parts), 50):
            batch = mention_parts[i : i + 50]
            documents.append(RawDocument(
                source="twitter", url=f"https://x.com/search?q={person.name}",
                content_raw="\n".join(batch),
                metadata={"type": "mentions", "batch": i // 50},
                fetched_at=datetime.utcnow(),
            ))

    cache.set(cache_key, [d.model_dump() for d in documents])
    return documents


# ──────────────────────────────────────────────────────────────────────────────
# 3. Playwright  (no auth, infinite scroll + GraphQL interception)
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_x_playwright(handle: str, max_tweets: int, cache: Cache) -> list[RawDocument]:
    url = f"https://x.com/{handle}"
    cache_key = f"xdotcom_v2_{handle}"
    cached = cache.get(cache_key)
    if cached:
        return [RawDocument(
            source="twitter", url=url, content_raw=cached,
            metadata={"handle": handle, "via": "x.com_cached"},
            fetched_at=datetime.utcnow(),
        )]

    try:
        from playwright.async_api import async_playwright
        from bs4 import BeautifulSoup

        graphql_tweets: list[str] = []

        def _parse_graphql_tweets(data: dict) -> list[str]:
            out: list[str] = []
            try:
                instructions = (
                    data.get("data", {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline_v2", {})
                    .get("timeline", {})
                    .get("instructions", [])
                )
                for instr in instructions:
                    for entry in instr.get("entries", []):
                        content = entry.get("content", {})
                        items = []
                        if "itemContent" in content:
                            items.append(content["itemContent"])
                        for it in content.get("items", []):
                            if "item" in it and "itemContent" in it["item"]:
                                items.append(it["item"]["itemContent"])
                        for item in items:
                            tweet_res = item.get("tweet_results", {}).get("result", {})
                            if tweet_res.get("__typename") == "TweetWithVisibilityResults":
                                tweet_res = tweet_res.get("tweet", tweet_res)
                            legacy = tweet_res.get("legacy", {})
                            text = legacy.get("full_text", "")
                            created = legacy.get("created_at", "")
                            if text and not text.startswith("RT "):
                                if created:
                                    try:
                                        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
                                        out.append(f"[{dt.strftime('%Y-%m-%d')}] {text}")
                                    except Exception:
                                        out.append(text)
                                else:
                                    out.append(text)
            except Exception as e:
                logger.debug(f"GraphQL parse error: {e}")
            return out

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            async def _on_response(response):
                try:
                    url_r = response.url
                    if ("UserTweets" in url_r or "UserTweetsAndReplies" in url_r) and response.status == 200:
                        body = await response.json()
                        new_tweets = _parse_graphql_tweets(body)
                        graphql_tweets.extend(new_tweets)
                except Exception:
                    pass

            page.on("response", _on_response)

            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            try:
                close = page.locator('[aria-label="Close"]').first
                if await close.is_visible(timeout=2000):
                    await close.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

            scroll_rounds = min(20, max(5, max_tweets // 15))
            prev_count = 0
            stall = 0
            for _ in range(scroll_rounds):
                if len(graphql_tweets) >= max_tweets:
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(1.8, 2.8))
                if len(graphql_tweets) == prev_count:
                    stall += 1
                    if stall >= 3:
                        break
                else:
                    stall = 0
                prev_count = len(graphql_tweets)

            html = await page.content()
            await browser.close()

        dom_tweets: list[str] = []
        if not graphql_tweets:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.select('[data-testid="tweetText"]'):
                text = el.get_text(" ", strip=True)
                if text and len(text) > 10:
                    dom_tweets.append(text)

        soup = BeautifulSoup(html, "html.parser")
        bio_el = soup.select_one('[data-testid="UserDescription"]')
        bio = bio_el.get_text(" ", strip=True) if bio_el else ""
        extras: list[str] = []
        for sel in ['[data-testid="UserName"]', '[data-testid="UserLocation"]']:
            el = soup.select_one(sel)
            if el:
                extras.append(el.get_text(" ", strip=True))

        tweets = graphql_tweets or dom_tweets
        if tweets or bio:
            parts = []
            if extras:
                parts.append("Profile info: " + " | ".join(extras))
            if bio:
                parts.append(f"Bio: {bio}")
            parts.extend(tweets[:max_tweets])
            content = "\n".join(parts)
            cache.set(cache_key, content)
            logger.info(f"X.com Playwright: {len(tweets)} tweets for @{handle}")
            return [RawDocument(
                source="twitter", url=url, content_raw=content,
                metadata={"handle": handle, "via": "playwright", "tweet_count": len(tweets)},
                fetched_at=datetime.utcnow(),
            )]
        else:
            logger.warning(f"X.com Playwright: no content for @{handle} (login wall?)")

    except Exception as e:
        logger.warning(f"X.com Playwright failed for @{handle}: {e}")

    return []


# ──────────────────────────────────────────────────────────────────────────────
# 4. DDG snippets  (last resort)
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_via_ddg_snippets(handle: str, name: str, cache: Cache) -> list[RawDocument]:
    cache_key = f"ddg_tweets_{handle}"
    cached = cache.get(cache_key)
    if cached:
        return [RawDocument(
            source="twitter", url=f"https://x.com/{handle}",
            content_raw=cached,
            metadata={"handle": handle, "via": "ddg_snippets"},
            fetched_at=datetime.utcnow(),
        )]

    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        snippets: list[str] = []
        for q in [f"site:x.com/{handle}", f'"{name}" x.com/{handle}']:
            try:
                for r in ddgs.text(q, max_results=8) or []:
                    snippet = r.get("body", "").strip()
                    title = r.get("title", "").strip()
                    if snippet and len(snippet) > 20:
                        snippets.append(f"{title}: {snippet}" if title else snippet)
                await asyncio.sleep(random.uniform(0.5, 1.2))
            except Exception as e:
                logger.debug(f"DDG query failed '{q}': {e}")

        if snippets:
            content = "\n\n".join(snippets)
            cache.set(cache_key, content)
            logger.info(f"DDG snippets: {len(snippets)} snippets for @{handle}")
            return [RawDocument(
                source="twitter", url=f"https://x.com/{handle}",
                content_raw=content,
                metadata={"handle": handle, "via": "ddg_snippets", "snippet_count": len(snippets)},
                fetched_at=datetime.utcnow(),
            )]
    except Exception as e:
        logger.warning(f"DDG snippet scrape failed: {e}")

    return []


# ──────────────────────────────────────────────────────────────────────────────
# 2. twikit  (free — regular X account, no API key needed)
# ──────────────────────────────────────────────────────────────────────────────

async def _scrape_via_twikit(person: PersonQuery, config: Config, cache: Cache) -> list[RawDocument]:
    """
    Scrape tweets using twikit — free, requires only a regular X account.
    No developer API key needed. Login with username+password; cookies are
    saved for subsequent runs so the password is only needed once.
    https://github.com/d60/twikit
    """
    try:
        from twikit import Client
    except ImportError:
        logger.warning("twikit not installed — run: pip install twikit")
        return []

    handle = person.twitter_handle
    cache_key = f"twikit_{handle or person.name}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"twikit: cache hit for @{handle or person.name}")
        return [RawDocument(
            source="twitter",
            url=f"https://x.com/{handle or ''}",
            content_raw=cached,
            metadata={"handle": handle, "via": "twikit_cached"},
            fetched_at=datetime.utcnow(),
        )]

    cookies_path = str(config.twikit_cookies_path)
    client = Client("en-US")
    logged_in = False

    # Try saved cookies first (avoids re-entering password every run)
    if config.twikit_cookies_path.exists():
        try:
            client.load_cookies(cookies_path)
            logged_in = True
            logger.info("twikit: loaded saved cookies")
        except Exception as e:
            logger.warning(f"twikit: failed to load cookies ({e}) — will re-login")

    if not logged_in:
        if not (config.twitter_username and config.twitter_password):
            logger.info("twikit: no credentials in config — set TWITTER_USERNAME and TWITTER_PASSWORD in .env")
            return []
        try:
            config.twikit_cookies_path.parent.mkdir(parents=True, exist_ok=True)
            await client.login(
                auth_info_1=config.twitter_username,
                password=config.twitter_password,
                cookies_file=cookies_path,  # auto-saves cookies after login
            )
            logged_in = True
            logger.info("twikit: login successful, cookies saved")
        except Exception as e:
            logger.warning(f"twikit: login failed: {e}")
            return []

    tweet_parts: list[str] = []

    # ── Fetch user's own tweets ────────────────────────────────────────────
    if handle:
        try:
            user = await client.get_user_by_screen_name(handle)
            if user:
                results = await client.get_user_tweets(user.id, "Tweets", count=40)
                page = 0
                while results and len(tweet_parts) < config.max_tweets:
                    for tweet in results:
                        text = getattr(tweet, "full_text", "") or ""
                        if not text or text.startswith("RT "):
                            continue
                        created = getattr(tweet, "created_at", "") or ""
                        date_str = created[:10] if created else ""
                        tweet_parts.append(f"[{date_str}] {text}" if date_str else text)
                    page += 1
                    if len(tweet_parts) >= config.max_tweets or page >= 10:
                        break
                    try:
                        results = await results.next()
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                    except Exception:
                        break
                logger.info(f"twikit: {len(tweet_parts)} tweets from @{handle}")
        except Exception as e:
            logger.warning(f"twikit: get_user_tweets failed for @{handle}: {e}")

    # ── Search for mentions and coverage ──────────────────────────────────
    try:
        search_q = f'"{person.name}"'
        if person.company:
            search_q += f" {person.company}"
        search_results = await client.search_tweet(search_q, "Latest", count=20)
        for tweet in (search_results or []):
            text = getattr(tweet, "full_text", "") or ""
            if not text:
                continue
            user_obj = getattr(tweet, "user", None)
            username = getattr(user_obj, "screen_name", "unknown") or "unknown"
            # Skip if it's the same handle we already collected
            if handle and username.lower() == handle.lower():
                continue
            created = getattr(tweet, "created_at", "") or ""
            date_str = created[:10] if created else ""
            tweet_parts.append(f"[{date_str}] @{username}: {text}")
        logger.info(f"twikit: search added {len(search_results or [])} results")
    except Exception as e:
        logger.debug(f"twikit: search failed: {e}")

    if not tweet_parts:
        logger.warning("twikit: 0 tweets collected")
        return []

    content = "\n".join(tweet_parts)
    cache.set(cache_key, content)

    docs: list[RawDocument] = []
    for i in range(0, len(tweet_parts), 50):
        batch = tweet_parts[i : i + 50]
        docs.append(RawDocument(
            source="twitter",
            url=f"https://x.com/{handle or ''}",
            content_raw="\n".join(batch),
            metadata={
                "handle": handle,
                "via": "twikit",
                "batch": i // 50,
                "tweet_count": len(batch),
            },
            fetched_at=datetime.utcnow(),
        ))

    logger.info(f"twikit: {len(tweet_parts)} total items → {len(docs)} docs")
    return docs


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

async def scrape(person: PersonQuery, config: Config, cache: Cache) -> list[RawDocument]:
    handle = person.twitter_handle

    # 1. socialdata.tools — cheapest paid, ~$0.05/1K tweets
    if config.socialdata_api_key and handle:
        logger.info(f"Twitter: using socialdata.tools for @{handle}")
        try:
            docs = await _scrape_via_socialdata(handle, config.max_tweets, config.socialdata_api_key, cache)
            if docs:
                return docs
            logger.warning("socialdata: 0 docs, falling through")
        except Exception as e:
            logger.warning(f"socialdata failed: {e}, falling through")

    # 2. TwitterAPI.io
    if config.twitterapi_io_key:
        if handle:
            logger.info(f"Twitter: using TwitterAPI.io for @{handle}")
            try:
                docs = await _scrape_via_twitterapi_io(handle, config.max_tweets, config.twitterapi_io_key, cache)
                if docs:
                    return docs
                logger.warning("TwitterAPI.io: 0 docs, falling through")
            except Exception as e:
                logger.warning(f"TwitterAPI.io failed: {e}, falling through")
        else:
            logger.info("Twitter: TwitterAPI.io key present but no handle provided — skipping")

    if config.twitter_api_only:
        logger.warning(
            "Twitter: API-only mode is enabled and no API scraper returned data. "
            "Skipping cookie/browser fallbacks."
        )
        return []

    # 2. X GraphQL — free, uses saved browser cookies (auth_token + ct0)
    cookies = load_x_cookies()
    if cookies and handle:
        auth_token, ct0 = cookies
        logger.info(f"Twitter: trying X GraphQL scraper for @{handle}")
        try:
            docs = await _scrape_via_x_graphql(handle, config.max_tweets, auth_token, ct0, cache)
            if docs:
                return docs
            logger.warning("Twitter: X GraphQL returned 0 docs, falling through")
        except Exception as e:
            logger.warning(f"Twitter: X GraphQL failed ({e}), falling through")
    else:
        logger.info("Twitter: no saved cookies — skipping X GraphQL (use twitter auth in UI to set up)")

    # 3. twscrape — manual cookie extraction via library
    try:
        import twscrape
        if config.twscrape_accounts_db.exists():
            api = twscrape.API(str(config.twscrape_accounts_db))
            accounts = await api.pool.get_all()
            active = [a for a in accounts if getattr(a, "active", True)]
            logger.info(f"Twitter: twscrape pool — {len(accounts)} accounts, {len(active)} active")
            if active:
                try:
                    docs = await _scrape_via_twscrape(person, config, cache)
                    if docs:
                        return docs
                    logger.warning("Twitter: twscrape returned 0 docs, falling through")
                except Exception as e:
                    logger.warning(f"Twitter: twscrape failed ({e}), falling through")
    except Exception as e:
        logger.debug(f"Twitter: twscrape check failed: {e}")

    if not handle:
        logger.info("Twitter: no handle — skipping Playwright and DDG")
        return []

    # 4. Playwright (no auth — public profiles only)
    logger.info(f"Twitter: trying Playwright for @{handle}")
    docs = await _scrape_x_playwright(handle, config.max_tweets, cache)
    if docs and any(d.content_raw.strip() for d in docs):
        return docs

    # 5. DDG snippets — last resort
    logger.info(f"Twitter: falling back to DDG snippets for @{handle}")
    return await _scrape_via_ddg_snippets(handle, person.name, cache)
