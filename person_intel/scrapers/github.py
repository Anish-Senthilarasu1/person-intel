from datetime import datetime

from loguru import logger

from person_intel.config import Config
from person_intel.storage.cache import Cache
from person_intel.storage.models import PersonQuery, RawDocument

source_name = "github"
requires_credentials = False


async def scrape(
    person: PersonQuery,
    config: Config,
    cache: Cache,
) -> list[RawDocument]:
    try:
        from github import Github, GithubException
    except ImportError:
        logger.warning("PyGithub not installed, skipping GitHub scraper")
        return []

    cache_key = f"github_{person.github_username or person.name}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("GitHub: loaded from cache")
        return [RawDocument(**d) for d in cached]

    g = Github(config.github_token) if config.github_authenticated else Github()
    documents: list[RawDocument] = []
    request_count = 0
    max_requests = 200 if config.github_authenticated else 40

    try:
        # Find user by username or search
        github_user = None
        if person.github_username:
            try:
                github_user = g.get_user(person.github_username)
                request_count += 1
            except GithubException:
                pass

        if not github_user:
            query = person.name
            if person.company:
                query += f" {person.company}"
            results = g.search_users(query)
            request_count += 1
            for u in results:
                if request_count >= max_requests:
                    break
                github_user = u
                request_count += 1
                break

        if not github_user:
            logger.info(f"GitHub: no user found for '{person.name}'")
            return []

        # User profile bio
        bio_parts = []
        if github_user.bio:
            bio_parts.append(f"Bio: {github_user.bio}")
        if github_user.company:
            bio_parts.append(f"Company: {github_user.company}")
        if github_user.blog:
            bio_parts.append(f"Blog: {github_user.blog}")
        if github_user.location:
            bio_parts.append(f"Location: {github_user.location}")
        bio_parts.append(f"Public repos: {github_user.public_repos}")
        bio_parts.append(f"Followers: {github_user.followers}")

        if bio_parts:
            documents.append(
                RawDocument(
                    source="github",
                    url=github_user.html_url,
                    content_raw="\n".join(bio_parts),
                    metadata={"type": "profile", "username": github_user.login},
                    fetched_at=datetime.utcnow(),
                )
            )

        # Top repositories
        repo_summaries = []
        repos = sorted(
            github_user.get_repos(),
            key=lambda r: r.stargazers_count,
            reverse=True,
        )
        request_count += 1

        for repo in repos[:10]:
            if request_count >= max_requests:
                break
            summary = (
                f"Repo: {repo.full_name} | Stars: {repo.stargazers_count} | "
                f"Lang: {repo.language} | Description: {repo.description or 'N/A'}"
            )
            repo_summaries.append(summary)

            # Fetch README for top repos
            if repo.stargazers_count > 10:
                try:
                    readme = repo.get_readme()
                    request_count += 1
                    readme_content = readme.decoded_content.decode("utf-8", errors="ignore")
                    if len(readme_content) > 100:
                        documents.append(
                            RawDocument(
                                source="github",
                                url=repo.html_url,
                                content_raw=readme_content[:8000],
                                metadata={
                                    "type": "readme",
                                    "repo": repo.full_name,
                                    "stars": repo.stargazers_count,
                                },
                                fetched_at=datetime.utcnow(),
                            )
                        )
                except GithubException:
                    pass

        if repo_summaries:
            documents.append(
                RawDocument(
                    source="github",
                    url=github_user.html_url,
                    content_raw="\n".join(repo_summaries),
                    metadata={"type": "repo_list", "username": github_user.login},
                    fetched_at=datetime.utcnow(),
                )
            )

        logger.info(
            f"GitHub: {len(documents)} documents for @{github_user.login} "
            f"({request_count} API calls)"
        )

    except Exception as e:
        logger.warning(f"GitHub scraper failed: {e}")

    cache.set(cache_key, [d.model_dump() for d in documents])
    return documents
