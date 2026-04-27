import asyncio
import json as _json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

import streamlit as st

st.set_page_config(
    page_title="Person Intel",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stTextInput > label, .stTextArea > label { font-weight: 600; }
.log-box { background:#0e1117; border-radius:6px; padding:0.8rem 1rem;
           border:1px solid #262730; font-family:monospace; font-size:0.8rem;
           color:#aaa; max-height:260px; overflow-y:auto; }
.log-line { margin:1px 0; }
.log-ok   { color:#4caf50; }
.log-warn { color:#ff9800; }
.log-err  { color:#f44336; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Raw dump builder  (no LLM — just formats everything scraped into markdown)
# ─────────────────────────────────────────────────────────────────────────────

def _build_raw_dump(person, chunks, generated_at: str) -> str:
    from person_intel.storage.models import Chunk

    lines: list[str] = []

    # Header
    lines.append(f"# {person.name} — Raw Intelligence Dump")
    lines.append(f"_Generated: {generated_at}_")
    if person.twitter_handle:
        lines.append(f"_X/Twitter: [@{person.twitter_handle}](https://x.com/{person.twitter_handle})_")
    if person.company:
        lines.append(f"_Company/Org: {person.company}_")
    lines.append("")

    # Stats
    by_source: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_source.setdefault(c.source, []).append(c)

    lines.append("## Summary")
    lines.append(f"- **Total chunks:** {len(chunks)}")
    for src, src_chunks in sorted(by_source.items()):
        total_tokens = sum(c.token_count for c in src_chunks)
        lines.append(f"- **{src.title()}:** {len(src_chunks)} chunk(s), ~{total_tokens:,} tokens")
    lines.append("")

    # Twitter — every tweet
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
                lines.append(f"_Source: @{handle}{f' · batch {batch}' if batch != '' else ''}{f' · via {via}' if via else ''}_")
                lines.append("")
            for tweet_line in chunk.content.splitlines():
                tweet_line = tweet_line.strip()
                if tweet_line:
                    lines.append(f"> {tweet_line}")
            lines.append("")

    # Web pages
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

    # GitHub
    if "github" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## GitHub")
        lines.append("")
        for chunk in by_source["github"]:
            lines.append(f"### [{chunk.url}]({chunk.url})")
            lines.append(chunk.content)
            lines.append("")

    # LinkedIn
    if "linkedin" in by_source:
        lines.append("---")
        lines.append("")
        lines.append("## LinkedIn")
        lines.append("")
        for chunk in by_source["linkedin"]:
            lines.append(chunk.content)
            lines.append("")

    # Sources list
    lines.append("---")
    lines.append("")
    lines.append("## Sources")
    seen_urls: set[str] = set()
    i = 1
    for chunk in chunks:
        if chunk.url not in seen_urls:
            seen_urls.add(chunk.url)
            lines.append(f"{i}. [{chunk.url}]({chunk.url})")
            i += 1

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline runner (background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline(person_data: dict, opts: dict, log_queue: Queue):
    """
    Runs every pipeline stage manually so we can stream status to the UI.
    opts keys: enabled_sources, build_rag, max_tweets, max_web_pages
    """
    def log(msg: str):
        log_queue.put(msg)

    try:
        from person_intel.config import Config
        from person_intel.processing.chunker import chunk_documents
        from person_intel.processing.dedup import deduplicate
        from person_intel.processing.ranker import ContextBudget
        from person_intel.rag.index import build_index
        from person_intel.scrapers.registry import SCRAPERS
        from person_intel.storage.cache import Cache
        from person_intel.storage.models import PersonQuery
        from person_intel.synthesis.assembler import build_profile, render_markdown
        from person_intel.synthesis.llm import summarize_chunk, synthesize_profile

        enabled_sources = opts.get("enabled_sources", ["web", "github"])
        build_rag = opts.get("build_rag", False)

        config = Config()
        config.max_tweets = opts.get("max_tweets", 200)
        config.max_web_pages = opts.get("max_web_pages", 20)
        if opts.get("twitterapiio_key"):
            config.twitterapi_io_key = opts["twitterapiio_key"]

        person = PersonQuery(**person_data)
        slug = person.slug
        output_dir = config.output_dir / slug
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "raw").mkdir(exist_ok=True)
        (output_dir / "chunks").mkdir(exist_ok=True)

        cache = Cache(output_dir / "cache", ttl_hours=config.cache_ttl_hours)

        # ── Stage 1: Scrape ───────────────────────────────────────────────
        log("Stage 1/5 — Scraping sources...")
        raw_docs = []

        # Use a fresh event loop in this thread to avoid conflicts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _scrape_all():
            tasks = {
                name: scraper.scrape(person, config, cache)
                for name, scraper in SCRAPERS.items()
                if name in enabled_sources
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            docs = []
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

        # Save raw
        for source in enabled_sources:
            src_docs = [d for d in raw_docs if d.source == source]
            if src_docs:
                (output_dir / "raw" / f"{source}.json").write_text(
                    _json.dumps([d.model_dump(mode="json") for d in src_docs], indent=2)
                )

        if not raw_docs:
            log("  ⚠ No documents collected. Check scraper errors above.")
            log("  Tip: Web scraper may be rate-limited by DuckDuckGo. Try again in 60s.")

        # ── Stage 2: Dedup ────────────────────────────────────────────────
        log("Stage 2/5 — Deduplicating...")
        deduped = deduplicate(raw_docs)
        log(f"  {len(raw_docs)} → {len(deduped)} unique docs")

        # ── Stage 3: Chunk ────────────────────────────────────────────────
        log("Stage 3/5 — Chunking...")
        chunks = chunk_documents(deduped)
        log(f"  {len(chunks)} chunks")
        (output_dir / "chunks" / "all_chunks.jsonl").write_text(
            "\n".join(c.model_dump_json() for c in chunks)
        )

        # ── Stage 4 & 5: Build output ─────────────────────────────────────
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        raw_dump_mode = opts.get("raw_dump", False)

        if raw_dump_mode:
            log("Stage 4/5 — Skipping AI (raw dump mode)...")
            log("Stage 5/5 — Building raw markdown dump...")
            final_md = _build_raw_dump(person, chunks, generated_at)
        else:
            log("Stage 4/5 — Context budget...")
            budget = ContextBudget(config.max_context_tokens)
            total = budget.total_tokens(chunks)
            log(f"  {total:,} tokens (limit {config.max_context_tokens:,})")

            final_chunks = chunks
            if chunks and not budget.fits(chunks):
                log("  Compressing chunks to fit context window...")
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                from person_intel.processing.ranker import ContextBudget as CB
                ranked = CB(config.max_context_tokens).rank_for_compression(chunks, person)
                final_chunks = list(chunks)
                from person_intel.storage.models import Chunk
                for chunk in ranked:
                    if budget.fits(final_chunks):
                        break
                    summary = summarize_chunk(chunk, person, config)
                    new_chunk = Chunk(
                        source=chunk.source, url=chunk.url, content=summary,
                        token_count=len(enc.encode(summary)),
                        metadata=chunk.metadata, is_summary=True,
                    )
                    final_chunks[final_chunks.index(chunk)] = new_chunk
                log(f"  Compressed to {budget.total_tokens(final_chunks):,} tokens")

            from person_intel.storage.models import PersonModel
            from person_intel.synthesis.llm import build_person_model

            log("Stage 5/5 — Building structured person model...")
            person_model = build_person_model(person, final_chunks, config)
            (output_dir / "person_model.json").write_text(person_model.model_dump_json(indent=2))
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
            log("  Building RAG index...")
            build_index(person, final_chunks, config.output_dir)
            log("  RAG index ready")

        cache.close()
        # Collect raw files for download
        raw_files: dict[str, str] = {}
        for src in enabled_sources:
            p = output_dir / "raw" / f"{src}.json"
            if p.exists():
                raw_files[src] = p.read_text()
        chunks_file = output_dir / "chunks" / "all_chunks.jsonl"
        if chunks_file.exists():
            raw_files["_chunks"] = chunks_file.read_text()
        person_model_file = output_dir / "person_model.json"
        if person_model_file.exists():
            raw_files["_person_model"] = person_model_file.read_text()

        log_queue.put(("__DONE__", str(output_file), final_md, raw_files))

    except Exception as e:
        tb = traceback.format_exc()
        log(f"  FATAL: {e}")
        log(tb)
        log_queue.put(("__ERROR__", str(e), ""))


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 Person Intel")
    st.caption("Deep-profile anyone — famous or not")
    st.divider()

    st.subheader("Who to profile")
    name    = st.text_input("Full name *",          placeholder="Rishi Kanaparti")
    twitter = st.text_input("X / Twitter handle",   placeholder="theirhandle  (no @)")
    linkedin= st.text_input("LinkedIn slug",         placeholder="rishikanaparti")
    github  = st.text_input("GitHub username",       placeholder="rishik")
    company = st.text_input("Company / school / org",placeholder="YC, MIT, Stripe…")

    st.divider()
    st.subheader("Sources")
    c1, c2 = st.columns(2)
    use_twitter  = c1.checkbox("X / Twitter",  value=True)
    use_web      = c2.checkbox("Web",           value=True)
    use_github   = c1.checkbox("GitHub",        value=True)
    use_linkedin = c2.checkbox("LinkedIn",      value=False)

    st.divider()
    st.subheader("Options")
    max_tweets = st.slider("Max tweets",    50,  500, 200, step=50)
    max_pages  = st.slider("Max web pages",  5,   50,  20, step=5)
    raw_dump   = st.checkbox("Raw dump (no AI — just all scraped data)", value=True,
                             help="Skips the LLM entirely. Gives you a massive markdown file with every tweet and web finding exactly as scraped.")
    build_rag  = st.checkbox("Build Q&A index (RAG)", value=False)

    st.divider()
    # TwitterAPI.io key input — persisted in session so user only enters once
    twitterapiio_key_input = st.text_input(
        "TwitterAPI.io key (optional)",
        value=st.session_state.get("twitterapiio_key", ""),
        type="password",
        help="Get a free key at twitterapi.io — costs ~$0.15/1K tweets. Leave blank to use free Playwright fallback.",
        key="twitterapiio_key_field",
    )
    if twitterapiio_key_input:
        st.session_state["twitterapiio_key"] = twitterapiio_key_input

    st.divider()
    with st.expander("🐦 Twitter setup (optional — unlocks ALL tweets)"):
        st.caption(
            "Without this, the scraper still gets tweets via Playwright (public profiles). "
            "Add your cookies to unlock every tweet via twscrape."
        )
        st.markdown("""
**One-time setup — paste your X browser cookies:**
1. Open **x.com** in Chrome while logged in to your account
2. Press `F12` → **Application tab** → **Storage → Cookies** → click `https://x.com`
3. Find `auth_token` → copy its **Value** (long hex string)
4. Find `ct0` → copy its **Value** (another long string)
        """)
        tw_user      = st.text_input("Your X username (no @)", key="tw_add_user")
        tw_auth      = st.text_input("auth_token cookie value", key="tw_auth_token", type="password")
        tw_ct0       = st.text_input("ct0 cookie value",        key="tw_ct0")

        col_test, col_add = st.columns(2)

        if col_add.button("Save cookies", key="tw_add_btn", type="primary"):
            if tw_user and tw_auth and tw_ct0:
                import asyncio as _aio
                async def _add():
                    import twscrape
                    from person_intel.config import Config as _C
                    cfg = _C()
                    cfg.twscrape_accounts_db.parent.mkdir(parents=True, exist_ok=True)
                    api = twscrape.API(str(cfg.twscrape_accounts_db))
                    cookies = {"auth_token": tw_auth.strip(), "ct0": tw_ct0.strip()}
                    # Add account with cookies — skips login flow entirely
                    await api.pool.add_account(
                        username=tw_user.strip(),
                        password="placeholder",
                        email="placeholder@placeholder.com",
                        email_password="placeholder",
                        cookies=cookies,
                    )
                loop = _aio.new_event_loop()
                try:
                    loop.run_until_complete(_add())
                    st.success(f"✓ @{tw_user} added via cookies!")
                except Exception as e:
                    st.error(f"Failed: {e}")
                finally:
                    loop.close()
            else:
                st.warning("Fill in username, auth_token, and ct0.")

        if col_test.button("Test connection", key="tw_test_btn"):
            import asyncio as _aio
            async def _test():
                import twscrape
                from person_intel.config import Config as _C
                cfg = _C()
                if not cfg.twscrape_accounts_db.exists():
                    return "No accounts saved yet.\n\nTip: The Playwright fallback still works without this — it scrapes public profiles automatically."
                api = twscrape.API(str(cfg.twscrape_accounts_db))
                accounts = await api.pool.get_all()
                if not accounts:
                    return "No accounts in pool.\n\nTip: Add cookies above to enable full tweet history."
                msgs = []
                for a in accounts:
                    msgs.append(f"@{a.username}: active={getattr(a, 'active', '?')}, cookies={'yes' if getattr(a, 'cookies', None) else 'no'}")
                try:
                    user = await api.user_by_login("sama")
                    msgs.append(f"\nFetch test: @sama → {user.displayname if user else 'FAILED'}")
                    if user:
                        msgs.append("✓ Cookie auth is working — full tweet history enabled!")
                except Exception as e:
                    msgs.append(f"\nFetch test FAILED: {e}")
                    msgs.append("Cookies may have expired — re-paste them from your browser.")
                return "\n".join(msgs)
            loop = _aio.new_event_loop()
            try:
                st.code(loop.run_until_complete(_test()))
            except Exception as e:
                st.error(str(e))
            finally:
                loop.close()

    st.divider()
    run_btn = st.button("▶  Run Profile", type="primary", use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main area
# ─────────────────────────────────────────────────────────────────────────────

st.title("Person Intelligence")

if not run_btn and "last_profile_md" not in st.session_state:
    st.info("Fill in a name (and any handles) in the sidebar, then hit **Run Profile**.")
    st.stop()

if run_btn:
    if not name.strip():
        st.error("Please enter a name.")
        st.stop()

    enabled_sources = (
        (["twitter"]  if use_twitter  else []) +
        (["web"]       if use_web      else []) +
        (["github"]    if use_github   else []) +
        (["linkedin"]  if use_linkedin else [])
    )

    person_data = {
        "name":            name.strip(),
        "twitter_handle":  twitter.strip()  or None,
        "linkedin_slug":   linkedin.strip() or None,
        "github_username": github.strip()   or None,
        "company":         company.strip()  or None,
    }
    opts = {
        "enabled_sources":   enabled_sources,
        "build_rag":         build_rag,
        "raw_dump":          raw_dump,
        "max_tweets":        max_tweets,
        "max_web_pages":     max_pages,
        "twitterapiio_key":  st.session_state.get("twitterapiio_key", ""),
    }

    # ── Live log display ──────────────────────────────────────────────────
    st.subheader(f"Running — {name}")
    log_placeholder = st.empty()
    log_lines: list[str] = []

    q: Queue = Queue()
    result_box: list = [None]

    def _worker():
        _run_pipeline(person_data, opts, q)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    done = False
    while not done:
        try:
            msg = q.get(timeout=0.4)
            if isinstance(msg, tuple):
                status, payload, md, *_rest = msg
                raw_files_result = _rest[0] if _rest else {}
                done = True
                if status == "__ERROR__":
                    st.error(f"Pipeline error: {payload}")
                    # Show full log even on error
                    with st.expander("Full log", expanded=True):
                        st.code("\n".join(log_lines))
                    st.stop()
                else:
                    result_box[0] = (payload, md, raw_files_result)
            else:
                log_lines.append(msg)
                html = "\n".join(
                    f'<div class="log-line">{l}</div>' for l in log_lines[-20:]
                )
                log_placeholder.markdown(
                    f'<div class="log-box">{html}</div>', unsafe_allow_html=True
                )
        except Empty:
            if not t.is_alive():
                done = True
    t.join()

    log_placeholder.empty()

    if not result_box[0]:
        st.error("Pipeline returned no result.")
        with st.expander("Full log"):
            st.code("\n".join(log_lines))
        st.stop()

    output_path, final_md, raw_files_result = result_box[0]
    st.session_state["last_profile_md"]   = final_md
    st.session_state["last_person_name"]  = name.strip()
    st.session_state["last_person_data"]  = person_data
    st.session_state["last_output_file"]  = output_path
    st.session_state["last_log"]          = log_lines
    st.session_state["last_raw_files"]    = raw_files_result
    st.success(f"Done — saved to `{output_path}`")
    st.subheader("Pipeline log")
    st.code("\n".join(log_lines))


# ─────────────────────────────────────────────────────────────────────────────
# Tabs: Profile | Q&A | Raw
# ─────────────────────────────────────────────────────────────────────────────

if "last_profile_md" not in st.session_state:
    st.stop()

profile_md   = st.session_state["last_profile_md"]
person_name  = st.session_state["last_person_name"]

tab_profile, tab_qa, tab_raw = st.tabs(["📄 Profile", "💬 Ask Questions", "🗂 Raw / Download"])

with tab_profile:
    lines = profile_md.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            display_md = "\n".join(lines[end + 1:]).strip()
        except ValueError:
            display_md = profile_md
    else:
        display_md = profile_md
    st.markdown(display_md)

with tab_qa:
    st.subheader(f"Ask anything about {person_name}")
    question = st.text_input("Question", placeholder="What do they care most about?")
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking..."):
            try:
                from person_intel.config import Config
                from person_intel.rag.index import query_index
                from person_intel.storage.models import PersonModel, PersonQuery
                from person_intel.synthesis.llm import answer_question

                cfg    = Config()
                person = PersonQuery(**st.session_state["last_person_data"])
                chunks = query_index(person, question, cfg.output_dir)
                if not chunks:
                    st.warning("No RAG index found. Re-run with **Build Q&A index** checked.")
                else:
                    person_model_path = cfg.output_dir / person.slug / "person_model.json"
                    if person_model_path.exists():
                        person_model = PersonModel.model_validate_json(person_model_path.read_text())
                    else:
                        person_model = PersonModel()
                    st.markdown(answer_question(question, person, chunks, person_model, cfg))
            except Exception as e:
                st.error(f"{e}")

with tab_raw:
    slug = person_name.lower().replace(" ", "-")
    fname = f"{slug}_profile.md"
    st.download_button("⬇  Download Profile (Markdown)", data=profile_md,
                       file_name=fname, mime="text/markdown")

    raw_files = st.session_state.get("last_raw_files", {})
    if raw_files:
        st.subheader("Raw scraped data")
        source_labels = {
            "twitter":  "🐦 Tweets (JSON)",
            "web":      "🌐 Web pages (JSON)",
            "github":   "🐙 GitHub (JSON)",
            "linkedin": "💼 LinkedIn (JSON)",
            "_chunks":  "📦 All chunks (JSONL)",
            "_person_model": "🧠 Structured person model (JSON)",
        }
        cols = st.columns(min(len(raw_files), 4))
        for i, (src, content) in enumerate(raw_files.items()):
            label = source_labels.get(src, f"{src} (JSON)")
            ext = "jsonl" if src == "_chunks" else "json"
            cols[i % 4].download_button(
                label=f"⬇  {label}",
                data=content,
                file_name=f"{slug}_{src}.{ext}",
                mime="application/json",
                key=f"dl_{src}",
            )

    if st.session_state.get("last_log"):
        with st.expander("Pipeline log"):
            st.code("\n".join(st.session_state["last_log"]))
    st.code(profile_md, language="markdown")
