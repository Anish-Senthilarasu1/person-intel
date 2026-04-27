from datetime import datetime

from person_intel.storage.models import Chunk, Profile, PersonQuery


def build_raw_markdown(
    person: PersonQuery,
    chunks: list[Chunk],
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# {person.name} — Raw Intelligence Dump")
    lines.append(f"_Generated: {generated_at}_")
    if person.twitter_handle:
        lines.append(f"_X/Twitter: [@{person.twitter_handle}](https://x.com/{person.twitter_handle})_")
    if person.company:
        lines.append(f"_Company/Org: {person.company}_")
    lines.append("")

    by_source: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_source.setdefault(chunk.source, []).append(chunk)

    lines.append("## Summary")
    lines.append(f"- **Total chunks:** {len(chunks)}")
    for source, source_chunks in sorted(by_source.items()):
        total_tokens = sum(chunk.token_count for chunk in source_chunks)
        lines.append(f"- **{source.title()}:** {len(source_chunks)} chunk(s), ~{total_tokens:,} tokens")
    lines.append("")

    if "twitter" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## Tweets")
        lines.append("")
        for chunk in by_source["twitter"]:
            handle = chunk.metadata.get("handle", person.twitter_handle or "")
            batch = chunk.metadata.get("batch", "")
            via = chunk.metadata.get("via", "")
            if handle:
                source_line = f"_Source: @{handle}"
                if batch != "":
                    source_line += f" · batch {batch}"
                if via:
                    source_line += f" · via {via}"
                lines.append(source_line + "_")
                lines.append("")
            for tweet_line in chunk.content.splitlines():
                tweet_line = tweet_line.strip()
                if tweet_line:
                    lines.append(f"> {tweet_line}")
            lines.append("")

    if "web" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## Web Pages")
        lines.append("")
        for chunk in by_source["web"]:
            lines.append(f"### [{chunk.url}]({chunk.url})")
            snippet = chunk.metadata.get("snippet", "")
            if snippet:
                lines.append(f"_{snippet[:200]}_")
                lines.append("")
            lines.append(chunk.content)
            lines.append("")

    if "github" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## GitHub")
        lines.append("")
        for chunk in by_source["github"]:
            lines.append(f"### [{chunk.url}]({chunk.url})")
            lines.append(chunk.content)
            lines.append("")

    if "linkedin" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## LinkedIn")
        lines.append("")
        for chunk in by_source["linkedin"]:
            lines.append(chunk.content)
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Sources")
    seen_urls: set[str] = set()
    index = 1
    for chunk in chunks:
        if chunk.url not in seen_urls:
            seen_urls.add(chunk.url)
            lines.append(f"{index}. [{chunk.url}]({chunk.url})")
            index += 1

    return "\n".join(lines)


def build_profile(
    person: PersonQuery,
    markdown: str,
    chunks: list[Chunk],
) -> Profile:
    sources = list({c.url for c in chunks})
    return Profile(
        person=person,
        markdown=markdown,
        sources_used=sources,
        total_documents=len(chunks),
        generated_at=datetime.utcnow(),
    )


def render_markdown(profile: Profile) -> str:
    front_matter = (
        "---\n"
        f"name: {profile.person.name}\n"
        f"generated_at: {profile.generated_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"total_documents: {profile.total_documents}\n"
        f"sources_count: {len(profile.sources_used)}\n"
        "---\n\n"
    )
    return front_matter + profile.markdown
