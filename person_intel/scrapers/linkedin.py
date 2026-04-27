import asyncio
import random
from datetime import datetime

from bs4 import BeautifulSoup
from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "linkedin"
requires_credentials = True


async def _scrape_with_login(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    from playwright.async_api import async_playwright

    slug = person.linkedin_slug or person.name.lower().replace(" ", "-")
    posts_url = f"https://www.linkedin.com/in/{slug}/recent-activity/shares/"

    cached = cache.get(posts_url)
    if cached is not None:
        logger.info("LinkedIn: loaded from cache")
        return [RawDocument(**d) for d in cached]

    documents: list[RawDocument] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            # Login
            await page.goto("https://www.linkedin.com/login", timeout=15000)
            await page.wait_for_load_state("networkidle")
            await page.fill("#username", config.linkedin_email)
            await page.fill("#password", config.linkedin_password)
            await page.click('[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(2, 4))

            # Check if login succeeded
            if "login" in page.url or "checkpoint" in page.url:
                logger.warning("LinkedIn: login failed or checkpoint triggered, skipping")
                await browser.close()
                return []

            # Navigate to posts page
            await page.goto(posts_url, timeout=15000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(2, 3))

            # Scroll to load more posts
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(1.5, 2.5))

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Extract post texts
            post_elements = soup.find_all(
                "span",
                attrs={"class": lambda c: c and "break-words" in c},
            )
            posts: list[str] = []
            for el in post_elements:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 50:
                    posts.append(text)

            if posts:
                batch_size = 20
                for i in range(0, len(posts), batch_size):
                    batch = posts[i : i + batch_size]
                    documents.append(
                        RawDocument(
                            source="linkedin",
                            url=posts_url,
                            content_raw="\n\n---\n\n".join(batch),
                            metadata={"slug": slug, "batch": i // batch_size},
                            fetched_at=datetime.utcnow(),
                        )
                    )

            logger.info(f"LinkedIn: extracted {len(posts)} posts for {slug}")

        except Exception as e:
            logger.warning(f"LinkedIn scraper failed: {e}")
        finally:
            await browser.close()

    cache.set(posts_url, [d.model_dump() for d in documents])
    return documents


async def scrape(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    if not config.linkedin_available:
        logger.info("LinkedIn: credentials not configured, skipping")
        return []

    return await _scrape_with_login(person, config, cache)
