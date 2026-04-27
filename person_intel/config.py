from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (Groq)
    groq_api_key: str
    # llama-3.1-8b-instant: 30K TPM free tier — handles large prompts
    # llama-3.3-70b-versatile: 12K TPM free tier — better quality but hits limits fast
    groq_model: str = "llama-3.1-8b-instant"
    groq_max_retries: int = 3
    # Soft limit per single LLM call (input tokens). Tweets are summarized in
    # batches first when total content exceeds this.
    groq_tpm_limit: int = 28000
    # Conservative per-request budget to stay below Groq's effective limit for
    # the selected model/tier. Used for prompt sizing, not overall dataset size.
    groq_request_token_limit: int | None = None

    # Twitter — socialdata.tools (~$0.05/1K tweets, cheapest paid option)
    socialdata_api_key: str | None = None
    # Twitter — TwitterAPI.io (pay-as-you-go ~$0.15/1K tweets)
    twitterapi_io_key: str | None = None
    # Twitter collection policy — keep this API-only unless you explicitly want
    # browser/cookie-based fallbacks.
    twitter_api_only: bool = True
    # Twitter — twscrape fallback (cookie-based, free but fragile)
    twscrape_accounts_db: Path = Path("./data/accounts.db")
    # Twitter — twikit (free, username+password, no API key needed)
    # https://github.com/d60/twikit — just a regular X account, no developer access required
    twitter_username: str | None = None   # X username or email
    twitter_password: str | None = None   # X account password
    twikit_cookies_path: Path = Path("./data/twikit_cookies.json")

    # LinkedIn (optional)
    linkedin_email: str | None = None
    linkedin_password: str | None = None

    # GitHub (optional)
    github_token: str | None = None

    # Web search — provider priority: Serper → Tavily → Google CSE → Brave → DDG
    # Serper.dev: 2,500 free Google searches on signup — https://serper.dev
    serper_api_key: str | None = None
    # Tavily: 1,000 free searches/month — https://tavily.com
    tavily_api_key: str | None = None
    # Google Programmable Search Engine: 100 free queries/day
    # Key: https://console.cloud.google.com  CX: https://programmablesearchengine.google.com
    google_cse_key: str | None = None
    google_cse_cx: str | None = None
    # Brave Search API (paid, ~$3/month) — https://api.search.brave.com/
    brave_search_api_key: str | None = None

    # Scraping limits
    max_tweets: int = 200
    max_web_pages: int = 20
    cache_ttl_hours: int = 24

    # Output
    output_dir: Path = Path("./outputs")
    max_context_tokens: int = 100_000  # Groq llama-3.3-70b has 128K ctx; leave headroom

    @property
    def linkedin_available(self) -> bool:
        return bool(self.linkedin_email and self.linkedin_password)

    @property
    def github_authenticated(self) -> bool:
        return bool(self.github_token)
