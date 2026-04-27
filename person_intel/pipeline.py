import asyncio
import json
from datetime import datetime
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.theme import Theme

from person_intel.config import Config
from person_intel.processing.chunker import chunk_documents
from person_intel.processing.dedup import deduplicate
from person_intel.processing.ranker import ContextBudget
from person_intel.knowledge import build_knowledge_pack
from person_intel.rag.index import build_index
from person_intel.scrapers.registry import SCRAPERS
from person_intel.storage.cache import Cache
from person_intel.storage.models import Chunk, PersonQuery, RawDocument
from person_intel.synthesis.assembler import build_profile, build_raw_markdown, render_markdown
from person_intel.synthesis.llm import summarize_chunk

console = Console(
    theme=Theme(
        {
            "progress.spinner": "bright_magenta",
            "progress.description": "bright_cyan",
            "progress.elapsed": "dim cyan",
        }
    )
)


async def _run_scrapers(
    person: PersonQuery,
    config: Config,
    cache: Cache,
    enabled_sources: list[str],
) -> list[RawDocument]:
    tasks = {}
    for name, scraper in SCRAPERS.items():
        if name not in enabled_sources:
            continue
        tasks[name] = scraper.scrape(person, config, cache)

    results: list[RawDocument] = []
    completed = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for name, result in zip(tasks.keys(), completed):
        if isinstance(result, Exception):
            logger.warning(f"Scraper '{name}' failed: {result}")
        else:
            logger.info(f"Scraper '{name}': {len(result)} documents")
            results.extend(result)

    return results


def _compress_chunks(
    chunks: list[Chunk],
    person: PersonQuery,
    config: Config,
) -> list[Chunk]:
    budget = ContextBudget(config.max_context_tokens)
    if budget.fits(chunks):
        return chunks

    total = budget.total_tokens(chunks)
    logger.info(
        f"Context budget exceeded: {total} tokens > {config.max_context_tokens}. "
        "Compressing low-priority chunks..."
    )

    ranked = budget.rank_for_compression(chunks, person)
    compressed: list[Chunk] = list(chunks)

    for chunk in ranked:
        if budget.fits(compressed):
            break
        summary_text = summarize_chunk(chunk, person, config)
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        new_chunk = Chunk(
            source=chunk.source,
            url=chunk.url,
            content=summary_text,
            token_count=len(enc.encode(summary_text)),
            metadata=chunk.metadata,
            is_summary=True,
        )
        idx = compressed.index(chunk)
        compressed[idx] = new_chunk
        logger.debug(
            f"Compressed chunk from {chunk.token_count} → {new_chunk.token_count} tokens"
        )

    logger.info(
        f"After compression: {budget.total_tokens(compressed)} tokens"
    )
    return compressed


def run(
    person: PersonQuery,
    config: Config,
    enabled_sources: list[str],
    build_rag: bool = False,
    no_cache: bool = False,
    raw_dump: bool = True,
) -> Path:
    slug = person.slug
    output_dir = config.output_dir / slug
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    cache_dir = output_dir / "cache" if not no_cache else output_dir / "cache_disabled"
    cache = Cache(cache_dir, ttl_hours=0 if no_cache else config.cache_ttl_hours)

    with Progress(
        SpinnerColumn(style="progress.spinner"),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        # Stage 1: Scraping
        task = progress.add_task("Scraping sources...", total=None)
        raw_docs = asyncio.run(
            _run_scrapers(person, config, cache, enabled_sources)
        )
        progress.update(task, description=f"Scraped {len(raw_docs)} raw documents")
        progress.stop_task(task)

        # Save raw
        for source in enabled_sources:
            source_docs = [d for d in raw_docs if d.source == source]
            if source_docs:
                (raw_dir / f"{source}.json").write_text(
                    json.dumps([d.model_dump(mode="json") for d in source_docs], indent=2)
                )

        # Stage 2: Dedup
        task = progress.add_task("Deduplicating...", total=None)
        deduped = deduplicate(raw_docs)
        progress.update(
            task,
            description=f"Deduped: {len(raw_docs)} → {len(deduped)} documents",
        )
        progress.stop_task(task)

        # Stage 3: Chunking
        task = progress.add_task("Chunking content...", total=None)
        chunks = chunk_documents(deduped)
        progress.update(task, description=f"Created {len(chunks)} chunks")
        progress.stop_task(task)

        # Save chunks
        chunks_file = output_dir / "chunks"
        chunks_file.mkdir(exist_ok=True)
        (chunks_file / "all_chunks.jsonl").write_text(
            "\n".join(c.model_dump_json() for c in chunks)
        )

        task = progress.add_task("Building knowledge pack...", total=None)
        knowledge_dir = build_knowledge_pack(person, chunks, config.output_dir)
        progress.update(task, description=f"Knowledge pack: {knowledge_dir}")
        progress.stop_task(task)

        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        final_chunks = chunks
        if raw_dump:
            task = progress.add_task("Formatting raw markdown dump...", total=None)
            final_md = build_raw_markdown(person, chunks, generated_at)
            progress.update(task, description="Raw intelligence file built")
            progress.stop_task(task)
        else:
            task = progress.add_task("Managing context budget...", total=None)
            final_chunks = _compress_chunks(chunks, person, config)
            from person_intel.processing.ranker import ContextBudget as CB
            budget = CB(config.max_context_tokens)
            progress.update(
                task,
                description=f"Context: {budget.total_tokens(final_chunks):,} tokens",
            )
            progress.stop_task(task)

            task = progress.add_task("Building structured person model...", total=None)
            from person_intel.synthesis.llm import build_person_model, synthesize_profile

            person_model = build_person_model(person, final_chunks, config)
            (output_dir / "person_model.json").write_text(
                person_model.model_dump_json(indent=2)
            )
            markdown = synthesize_profile(person, final_chunks, person_model, config, generated_at)
            progress.update(task, description="Person model synthesized")
            progress.stop_task(task)

            task = progress.add_task("Writing output...", total=None)
            profile = build_profile(person, markdown, final_chunks)
            final_md = render_markdown(profile)

        output_file = output_dir / f"{slug}_profile.md"
        output_file.write_text(final_md)
        progress.update(task, description=f"Saved: {output_file}")
        progress.stop_task(task)

        # Stage 7: Optional RAG
        if build_rag:
            task = progress.add_task("Building RAG index...", total=None)
            build_index(person, final_chunks, config.output_dir)
            progress.update(task, description="RAG index built")
            progress.stop_task(task)

    cache.close()
    return output_file
