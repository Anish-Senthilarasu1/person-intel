"""
FastAPI backend for Person Intel.
Run with: uvicorn server:app --reload --port 8000
Install:  pip install fastapi uvicorn[standard]
"""
from __future__ import annotations
import asyncio
import json
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Person Intel API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory job store ──────────────────────────────────────────────────────
jobs: dict[str, dict[str, Any]] = {}


# ─── Pydantic models ──────────────────────────────────────────────────────────
class ProfileRequest(BaseModel):
    name: str
    twitter_handle: Optional[str] = None
    linkedin_slug: Optional[str] = None
    github_username: Optional[str] = None
    company: Optional[str] = None
    enabled_sources: List[str] = ["web", "github"]
    max_tweets: int = 200
    max_web_pages: int = 20
    raw_dump: bool = False
    build_rag: bool = False
    twitterapiio_key: Optional[str] = None


class TwitterCookiesRequest(BaseModel):
    username: str
    auth_token: str
    ct0: str


class QARequest(BaseModel):
    question: str
    person: dict[str, Any]


# ─── Raw dump builder ─────────────────────────────────────────────────────────
def _build_raw_dump(person: Any, chunks: list, generated_at: str) -> str:
    lines: list[str] = []
    lines.append(f"# {person.name} — Raw Intelligence Dump")
    lines.append(f"_Generated: {generated_at}_")
    if person.twitter_handle:
        lines.append(f"_X/Twitter: [@{person.twitter_handle}](https://x.com/{person.twitter_handle})_")
    if person.company:
        lines.append(f"_Company/Org: {person.company}_")
    lines.append("")

    by_source: dict[str, list] = {}
    for c in chunks:
        by_source.setdefault(c.source, []).append(c)

    lines.append("## Summary")
    lines.append(f"- **Total chunks:** {len(chunks)}")
    for src, src_chunks in sorted(by_source.items()):
        total_tokens = sum(c.token_count for c in src_chunks)
        lines.append(f"- **{src.title()}:** {len(src_chunks)} chunk(s), ~{total_tokens:,} tokens")
    lines.append("")

    if "twitter" in by_source:
        lines.append("---\n\n## Tweets\n")
        for chunk in by_source["twitter"]:
            handle = chunk.metadata.get("handle", person.twitter_handle or "")
            batch = chunk.metadata.get("batch", "")
            via = chunk.metadata.get("via", "")
            if handle:
                label = f"_Source: @{handle}"
                if batch != "":
                    label += f" · batch {batch}"
                if via:
                    label += f" · via {via}"
                lines.append(label + "_\n")
            for tweet_line in chunk.content.splitlines():
                tweet_line = tweet_line.strip()
                if tweet_line:
                    lines.append(f"> {tweet_line}")
            lines.append("")

    if "web" in by_source:
        lines.append("---\n\n## Web Pages\n")
        for chunk in by_source["web"]:
            lines.append(f"### [{chunk.url}]({chunk.url})")
            snippet = chunk.metadata.get("snippet", "")
            if snippet:
                lines.append(f"_{snippet[:200]}_\n")
            lines.append(chunk.content)
            lines.append("")

    if "github" in by_source:
        lines.append("---\n\n## GitHub\n")
        for chunk in by_source["github"]:
            lines.append(f"### [{chunk.url}]({chunk.url})")
            lines.append(chunk.content)
            lines.append("")

    if "linkedin" in by_source:
        lines.append("---\n\n## LinkedIn\n")
        for chunk in by_source["linkedin"]:
            lines.append(chunk.content)
            lines.append("")

    lines.append("---\n\n## Sources")
    seen_urls: set[str] = set()
    i = 1
    for chunk in chunks:
        if chunk.url not in seen_urls:
            seen_urls.add(chunk.url)
            lines.append(f"{i}. [{chunk.url}]({chunk.url})")
            i += 1

    return "\n".join(lines)


# ─── Pipeline runner (background thread) ─────────────────────────────────────
def _run_pipeline(req_data: dict, q: Queue) -> None:
    def log(msg: str) -> None:
        q.put(msg)

    try:
        from person_intel.config import Config
        from person_intel.processing.chunker import chunk_documents
        from person_intel.processing.dedup import deduplicate
        from person_intel.processing.ranker import ContextBudget
        from person_intel.scrapers.registry import SCRAPERS
        from person_intel.storage.cache import Cache
        from person_intel.storage.models import PersonModel, PersonQuery
        from person_intel.synthesis.assembler import build_profile, render_markdown
        from person_intel.synthesis.llm import build_person_model, summarize_chunk, synthesize_profile

        enabled_sources: list[str] = req_data.get("enabled_sources", ["web", "github"])
        build_rag: bool = req_data.get("build_rag", False)

        config = Config()
        config.max_tweets = req_data.get("max_tweets", 200)
        config.max_web_pages = req_data.get("max_web_pages", 20)
        if req_data.get("twitterapiio_key"):
            config.twitterapi_io_key = req_data["twitterapiio_key"]

        person = PersonQuery(
            name=req_data["name"],
            twitter_handle=req_data.get("twitter_handle"),
            linkedin_slug=req_data.get("linkedin_slug"),
            github_username=req_data.get("github_username"),
            company=req_data.get("company"),
        )
        slug = person.slug
        output_dir = config.output_dir / slug
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "raw").mkdir(exist_ok=True)
        (output_dir / "chunks").mkdir(exist_ok=True)

        cache = Cache(output_dir / "cache", ttl_hours=config.cache_ttl_hours)

        # ── Stage 1: Scrape ───────────────────────────────────────────────
        log("stage:1")
        log("Stage 1/5 — Scraping sources...")
        raw_docs: list = []

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _scrape_all() -> list:
            tasks = {
                name: scraper.scrape(person, config, cache)
                for name, scraper in SCRAPERS.items()
                if name in enabled_sources
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            docs: list = []
            for name, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    log(f"  ✗ {name}: {result}")
                else:
                    log(f"  ✓ {name}: {len(result)} docs")
                    docs.extend(result)
            return docs

        try:
            raw_docs = loop.run_until_complete(_scrape_all())
        finally:
            loop.close()

        log(f"  Total raw: {len(raw_docs)} documents")

        for source in enabled_sources:
            src_docs = [d for d in raw_docs if d.source == source]
            if src_docs:
                (output_dir / "raw" / f"{source}.json").write_text(
                    json.dumps([d.model_dump(mode="json") for d in src_docs], indent=2)
                )

        if not raw_docs:
            log("  ⚠ No documents collected. Check scraper errors above.")
            log("  Tip: Web scraper may be rate-limited. Try again in 60 seconds.")

        # ── Stage 2: Dedup ────────────────────────────────────────────────
        log("stage:2")
        log("Stage 2/5 — Deduplicating...")
        deduped = deduplicate(raw_docs)
        log(f"  {len(raw_docs)} → {len(deduped)} unique docs")

        # ── Stage 3: Chunk ────────────────────────────────────────────────
        log("stage:3")
        log("Stage 3/5 — Chunking...")
        chunks = chunk_documents(deduped)
        log(f"  {len(chunks)} chunks")
        (output_dir / "chunks" / "all_chunks.jsonl").write_text(
            "\n".join(c.model_dump_json() for c in chunks)
        )

        # ── Stage 4 & 5: Build output ─────────────────────────────────────
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        raw_dump_mode: bool = req_data.get("raw_dump", False)
        final_chunks = chunks
        person_model = PersonModel()

        if raw_dump_mode:
            log("stage:4")
            log("Stage 4/5 — Skipping AI synthesis (raw dump mode)...")
            log("stage:5")
            log("Stage 5/5 — Building raw markdown dump...")
            final_md = _build_raw_dump(person, chunks, generated_at)
        else:
            log("stage:4")
            log("Stage 4/5 — Calculating context budget...")
            budget = ContextBudget(config.max_context_tokens)
            total = budget.total_tokens(chunks)
            log(f"  {total:,} tokens (limit {config.max_context_tokens:,})")

            if chunks and not budget.fits(chunks):
                log("  Compressing chunks to fit context window...")
                import tiktoken

                from person_intel.processing.ranker import ContextBudget as CB
                from person_intel.storage.models import Chunk

                enc = tiktoken.get_encoding("cl100k_base")
                ranked = CB(config.max_context_tokens).rank_for_compression(chunks, person)
                final_chunks = list(chunks)
                for chunk in ranked:
                    if budget.fits(final_chunks):
                        break
                    summary = summarize_chunk(chunk, person, config)
                    new_chunk = Chunk(
                        source=chunk.source,
                        url=chunk.url,
                        content=summary,
                        token_count=len(enc.encode(summary)),
                        metadata=chunk.metadata,
                        is_summary=True,
                    )
                    final_chunks[final_chunks.index(chunk)] = new_chunk
                log(f"  Compressed to {budget.total_tokens(final_chunks):,} tokens")

            log("stage:5")
            log("Stage 5/5 — Building structured person model...")
            person_model = build_person_model(person, final_chunks, config)
            person_model_path = output_dir / "person_model.json"
            person_model_path.write_text(person_model.model_dump_json(indent=2))
            log(
                f"  Extracted {len(person_model.observations)} observations, "
                f"{len(person_model.timeline)} timeline events, "
                f"{len(person_model.success_hypotheses)} success hypotheses"
            )
            log("  Synthesizing final person model report...")
            markdown = synthesize_profile(person, final_chunks, person_model, config, generated_at)
            profile = build_profile(person, markdown, final_chunks)
            final_md = render_markdown(profile)

        output_file = output_dir / f"{slug}_profile.md"
        output_file.write_text(final_md)
        log(f"  Saved → {output_file}")

        if build_rag and final_chunks:
            from person_intel.rag.index import build_index
            log("  Building RAG index...")
            build_index(person, final_chunks, config.output_dir)
            log("  RAG index ready")

        cache.close()

        # Collect stats
        by_source_count: dict[str, int] = {}
        total_tokens_count = 0
        for chunk in chunks:
            by_source_count[chunk.source] = by_source_count.get(chunk.source, 0) + 1
            total_tokens_count += chunk.token_count

        # Collect raw file paths for download endpoints
        raw_file_paths: dict[str, str] = {}
        for src in enabled_sources:
            p = output_dir / "raw" / f"{src}.json"
            if p.exists():
                raw_file_paths[src] = str(p)
        chunks_path = output_dir / "chunks" / "all_chunks.jsonl"
        if chunks_path.exists():
            raw_file_paths["_chunks"] = str(chunks_path)
        person_model_path = output_dir / "person_model.json"
        if person_model_path.exists():
            raw_file_paths["_person_model"] = str(person_model_path)

        q.put((
            "__DONE__",
            str(output_file),
            final_md,
            raw_file_paths,
            {
                "total_chunks": len(chunks),
                "total_tokens": total_tokens_count,
                "by_source": by_source_count,
                "observation_count": len(person_model.observations),
                "timeline_count": len(person_model.timeline),
                "hypothesis_count": len(person_model.success_hypotheses),
            },
        ))

    except Exception as e:
        tb = traceback.format_exc()
        log(f"  FATAL: {e}")
        log(tb)
        q.put(("__ERROR__", str(e), ""))


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/profile/start")
async def start_profile(req: ProfileRequest) -> dict:
    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    jobs[job_id] = {"status": "running", "queue": q, "result": None}

    def _worker() -> None:
        _run_pipeline(req.model_dump(), q)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"job_id": job_id}


@app.get("/api/profile/{job_id}/stream")
async def stream_profile(job_id: str) -> StreamingResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        q: Queue = jobs[job_id]["queue"]
        while True:
            try:
                msg = q.get(timeout=0.3)
                if isinstance(msg, tuple):
                    event_type = msg[0]
                    if event_type == "__DONE__":
                        _, output_path, final_md, raw_file_paths, stats = msg
                        jobs[job_id]["status"] = "done"
                        jobs[job_id]["result"] = {
                            "output_path": output_path,
                            "markdown": final_md,
                            "raw_file_paths": raw_file_paths,
                            "stats": stats,
                        }
                        data = json.dumps({
                            "type": "done",
                            "markdown": final_md,
                            "stats": stats,
                            "output_path": output_path,
                        })
                        yield f"data: {data}\n\n"
                        return
                    elif event_type == "__ERROR__":
                        error_msg = msg[1]
                        jobs[job_id]["status"] = "error"
                        yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"
                        return
                else:
                    yield f"data: {json.dumps({'type': 'log', 'message': str(msg)})}\n\n"
            except Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                await asyncio.sleep(0.15)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/profile/{job_id}/download/md")
async def download_md(job_id: str) -> FileResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    result = jobs[job_id].get("result")
    if not result:
        raise HTTPException(status_code=425, detail="Job not complete")
    path = Path(result["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="text/markdown", filename=path.name)


@app.get("/api/profile/{job_id}/download/{source}")
async def download_source(job_id: str, source: str) -> FileResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404)
    result = jobs[job_id].get("result")
    if not result:
        raise HTTPException(status_code=425, detail="Job not complete")
    raw_paths: dict = result.get("raw_file_paths", {})
    if source not in raw_paths:
        raise HTTPException(status_code=404, detail=f"Source '{source}' not available")
    path = Path(raw_paths[source])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    ext = "jsonl" if source == "_chunks" else "json"
    return FileResponse(path, media_type="application/json", filename=f"{source}.{ext}")


@app.post("/api/twitter/cookies")
async def save_twitter_cookies(req: TwitterCookiesRequest) -> dict:
    try:
        from person_intel.scrapers.twitter import save_x_cookies
        save_x_cookies(req.auth_token.strip(), req.ct0.strip())
        return {"success": True, "message": f"Cookies saved for @{req.username}. Twitter scraping is ready."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/twitter/test")
async def test_twitter() -> dict:
    try:
        import httpx
        from person_intel.scrapers.twitter import load_x_cookies, _x_headers, _X_BEARER
        import json

        cookies = load_x_cookies()
        if not cookies:
            return {"status": "no_cookies", "message": "No cookies saved yet. Paste auth_token + ct0 in the Twitter auth modal."}

        auth_token, ct0 = cookies
        headers = _x_headers(auth_token, ct0)

        # Quick test: resolve a known public user
        variables = json.dumps({"screen_name": "x", "withSafetyModeUserFields": True})
        features = json.dumps({"responsive_web_graphql_exclude_directive_enabled": True, "verified_phone_label_enabled": False, "responsive_web_graphql_timeline_navigation_enabled": True, "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False})

        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            r = await client.get(
                "https://twitter.com/i/api/graphql/G3KGOASz96M-Qu0nwmGXNg/UserByScreenName",
                params={"variables": variables, "features": features},
            )

        if r.status_code == 200:
            return {"status": "ok", "message": "Cookies valid — X scraper is ready."}
        elif r.status_code == 401:
            return {"status": "error", "message": "Cookies expired — re-extract from your browser."}
        else:
            return {"status": "error", "message": f"Unexpected status {r.status_code} — cookies may be stale."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TwikitLoginRequest(BaseModel):
    username: str            # X @handle (no @)
    email: str               # account email — required by X's bot-detection flow
    password: str            # X account password
    totp_secret: Optional[str] = None


@app.post("/api/twitter/twikit-login")
async def twikit_login(req: TwikitLoginRequest) -> dict:
    """
    Login to X via twikit. X requires auth_info_1=email + auth_info_2=username
    to pass its bot-detection JS challenge.
    """
    try:
        from twikit import Client
        from person_intel.config import Config

        cfg = Config()
        cfg.twikit_cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies_path = str(cfg.twikit_cookies_path)

        client = Client("en-US")
        kwargs: dict = {
            "auth_info_1": req.email.strip(),
            "auth_info_2": req.username.strip().lstrip("@"),
            "password": req.password.strip(),
            "cookies_file": cookies_path,
        }
        if req.totp_secret:
            kwargs["totp_secret"] = req.totp_secret.strip()
        await client.login(**kwargs)
        handle = req.username.strip().lstrip("@")
        user = await client.get_user_by_screen_name(handle)
        return {
            "success": True,
            "message": f"Logged in — cookies saved to {cookies_path}",
            "username": getattr(user, "screen_name", handle),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/twitter/twikit-test")
async def twikit_test() -> dict:
    """Check whether saved twikit cookies are still valid."""
    try:
        from twikit import Client
        from person_intel.config import Config

        cfg = Config()
        if not cfg.twikit_cookies_path.exists():
            return {"status": "no_cookies", "message": "No saved cookies — log in first."}

        client = Client("en-US")
        client.load_cookies(str(cfg.twikit_cookies_path))
        await client.get_user_by_screen_name("x")  # lightweight public lookup
        return {
            "status": "ok",
            "message": "Cookies valid — twikit is ready to scrape.",
            "cookies_path": str(cfg.twikit_cookies_path),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/qa")
async def ask_question(body: QARequest) -> dict:
    try:
        from person_intel.config import Config
        from person_intel.rag.index import query_index
        from person_intel.storage.models import PersonModel, PersonQuery
        from person_intel.synthesis.llm import answer_question

        cfg = Config()
        person = PersonQuery(**body.person)
        chunks = query_index(person, body.question, cfg.output_dir)
        if not chunks:
            return {"answer": None, "error": "No RAG index found. Re-run with 'Build Q&A index' enabled."}
        person_model_path = cfg.output_dir / person.slug / "person_model.json"
        if person_model_path.exists():
            person_model = PersonModel.model_validate_json(person_model_path.read_text())
        else:
            person_model = PersonModel()
        answer = answer_question(body.question, person, chunks, person_model, cfg)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
