from person_intel.scrapers import hackernews, linkedin, reddit, twitter, web

# Map source name → scraper module (each has scrape(), source_name, requires_credentials)
SCRAPERS = {
    "twitter": twitter,
    "web": web,
    "linkedin": linkedin,
    "hackernews": hackernews,
    "reddit": reddit,
}
