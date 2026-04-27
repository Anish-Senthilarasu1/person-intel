"""
LLM synthesis layer using Groq.

Large tweet sets are handled with a two-pass approach:
  1. Summarize tweet batches into dense digests (small calls, each < TPM limit)
  2. Synthesize the full profile from digests + web/github content (one final call)

This lets us analyze any number of tweets regardless of model TPM limits.
"""
import json
import re
import time
from pathlib import Path

import tiktoken
import tenacity
from groq import Groq, RateLimitError, APIStatusError
from jinja2 import Environment, FileSystemLoader
from loguru import logger

from person_intel.config import Config
from person_intel.storage.models import Chunk, PersonModel, PersonQuery

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_enc = tiktoken.get_encoding("cl100k_base")
_MODEL_REQUEST_LIMITS = {
    "llama-3.1-8b-instant": 6000,
    "llama-3.3-70b-versatile": 12000,
}


def _token_count(text: str) -> int:
    return len(_enc.encode(text))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    tokens = _enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _enc.decode(tokens[:max_tokens])


def _effective_request_limit(config: Config) -> int:
    if config.groq_request_token_limit:
        return config.groq_request_token_limit
    return _MODEL_REQUEST_LIMITS.get(config.groq_model, min(config.groq_tpm_limit, 8000))


def _get_jinja_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


def _make_client(config: Config) -> Groq:
    return Groq(api_key=config.groq_api_key)


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((RateLimitError, APIStatusError)),
    wait=tenacity.wait_exponential(multiplier=2, min=5, max=120),
    stop=tenacity.stop_after_attempt(4),
    before_sleep=lambda rs: logger.warning(
        f"LLM rate limit / error, retrying in {rs.next_action.sleep:.0f}s…"
    ),
)
def _call_llm(client: Groq, model: str, prompt: str, max_tokens: int = 4096) -> str:
    completion = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content


# ──────────────────────────────────────────────────────────────────────────────
# Tweet pre-summarization
# ──────────────────────────────────────────────────────────────────────────────

_TWEET_DIGEST_PROMPT = """\
You are analysing tweets by {name} to build a profile of who they are.

From the tweets below, extract and preserve in a dense bullet-point digest:
- Their stated opinions, beliefs, and values
- What they're working on or interested in
- Career / background clues
- Personality and communication style
- 5-10 of their most interesting direct quotes (verbatim, with date if present)
- Any notable events, announcements, or pivots mentioned

Do NOT summarise generically. Be specific and keep real quotes.

TWEETS ({count} total, source: @{handle}):
{content}

DIGEST:"""

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _summarize_tweet_batch(
    tweets_text: str, tweet_count: int, person: PersonQuery, client: Groq, model: str
) -> str:
    prompt = _TWEET_DIGEST_PROMPT.format(
        name=person.name,
        handle=person.twitter_handle or person.name,
        count=tweet_count,
        content=tweets_text,
    )
    return _call_llm(client, model, prompt, max_tokens=2048)


def _fit_tweet_batch_for_summary(
    tweets_text: str,
    tweet_count: int,
    person: PersonQuery,
    config: Config,
) -> tuple[str, int]:
    request_limit = _effective_request_limit(config)
    prompt_overhead = _token_count(
        _TWEET_DIGEST_PROMPT.format(
            name=person.name,
            handle=person.twitter_handle or person.name,
            count=tweet_count,
            content="",
        )
    )
    content_budget = max(500, request_limit - prompt_overhead - 2200)
    fitted_text = _truncate_to_tokens(tweets_text, content_budget)
    fitted_count = len([line for line in fitted_text.splitlines() if line.strip()])
    if fitted_text != tweets_text:
        logger.warning(
            f"Trimmed tweet digest batch from {tweet_count} tweets to {fitted_count} "
            f"to fit request budget"
        )
    return fitted_text, fitted_count


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    fenced = _JSON_BLOCK_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse JSON response: {exc}")
        return {}


def _normalize_person_model_payload(payload: dict) -> dict:
    def _normalize_evidence(items: list[dict] | None) -> list[dict]:
        normalized: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "source": item.get("source") or "unknown",
                    "url": item.get("url") or "",
                    "quote": item.get("quote") or None,
                    "confidence": item.get("confidence", 0.5) or 0.5,
                    "timestamp": item.get("timestamp") or None,
                }
            )
        return normalized

    normalized = {
        "observations": [],
        "timeline": [],
        "success_hypotheses": [],
        "contradictions": [str(x) for x in payload.get("contradictions", []) if x],
        "open_questions": [str(x) for x in payload.get("open_questions", []) if x],
    }

    for obs in payload.get("observations", []):
        if not isinstance(obs, dict) or not obs.get("summary"):
            continue
        normalized["observations"].append(
            {
                "kind": obs.get("kind") or "observation",
                "summary": str(obs.get("summary")),
                "rationale": obs.get("rationale") or None,
                "confidence": obs.get("confidence", 0.5) or 0.5,
                "importance": obs.get("importance", 3) or 3,
                "tags": [str(x) for x in obs.get("tags", []) if x],
                "evidence": _normalize_evidence(obs.get("evidence")),
            }
        )

    for event in payload.get("timeline", []):
        if not isinstance(event, dict) or not event.get("event"):
            continue
        normalized["timeline"].append(
            {
                "date_label": event.get("date_label") or "Unknown date",
                "event": str(event.get("event")),
                "significance": event.get("significance") or None,
                "confidence": event.get("confidence", 0.5) or 0.5,
                "evidence": _normalize_evidence(event.get("evidence")),
            }
        )

    for hypothesis in payload.get("success_hypotheses", []):
        if not isinstance(hypothesis, dict) or not hypothesis.get("title") or not hypothesis.get("mechanism"):
            continue
        normalized["success_hypotheses"].append(
            {
                "title": str(hypothesis.get("title")),
                "mechanism": str(hypothesis.get("mechanism")),
                "confidence": hypothesis.get("confidence", 0.5) or 0.5,
                "supporting_observation_kinds": [
                    str(x) for x in hypothesis.get("supporting_observation_kinds", []) if x
                ],
                "evidence": _normalize_evidence(hypothesis.get("evidence")),
            }
        )

    return normalized


def _compact_extraction_payload(payload: dict) -> dict:
    normalized = _normalize_person_model_payload(payload)

    def _trim_text(text: str | None, limit: int) -> str | None:
        if not text:
            return None
        text = " ".join(str(text).split())
        return text[:limit]

    compact = {
        "observations": [],
        "timeline": [],
        "success_hypotheses": [],
        "contradictions": normalized["contradictions"][:3],
        "open_questions": normalized["open_questions"][:3],
    }

    observations = sorted(
        normalized["observations"],
        key=lambda obs: (obs.get("importance", 0), obs.get("confidence", 0.0)),
        reverse=True,
    )[:4]
    for obs in observations:
        compact["observations"].append(
            {
                "kind": obs["kind"],
                "summary": _trim_text(obs["summary"], 180),
                "rationale": _trim_text(obs.get("rationale"), 220),
                "confidence": obs["confidence"],
                "importance": obs["importance"],
                "tags": obs["tags"][:4],
                "evidence": [
                    {
                        "source": ev["source"],
                        "url": ev["url"],
                        "quote": _trim_text(ev.get("quote"), 120),
                        "confidence": ev["confidence"],
                        "timestamp": ev.get("timestamp"),
                    }
                    for ev in obs["evidence"][:1]
                ],
            }
        )

    for event in normalized["timeline"][:2]:
        compact["timeline"].append(
            {
                "date_label": _trim_text(event["date_label"], 40) or "Unknown date",
                "event": _trim_text(event["event"], 140),
                "significance": _trim_text(event.get("significance"), 160),
                "confidence": event["confidence"],
                "evidence": [
                    {
                        "source": ev["source"],
                        "url": ev["url"],
                        "quote": _trim_text(ev.get("quote"), 100),
                        "confidence": ev["confidence"],
                        "timestamp": ev.get("timestamp"),
                    }
                    for ev in event["evidence"][:1]
                ],
            }
        )

    for hypothesis in normalized["success_hypotheses"][:2]:
        compact["success_hypotheses"].append(
            {
                "title": _trim_text(hypothesis["title"], 80),
                "mechanism": _trim_text(hypothesis["mechanism"], 180),
                "confidence": hypothesis["confidence"],
                "supporting_observation_kinds": hypothesis["supporting_observation_kinds"][:3],
                "evidence": [
                    {
                        "source": ev["source"],
                        "url": ev["url"],
                        "quote": _trim_text(ev.get("quote"), 100),
                        "confidence": ev["confidence"],
                        "timestamp": ev.get("timestamp"),
                    }
                    for ev in hypothesis["evidence"][:1]
                ],
            }
        )

    return compact


def _fit_extraction_payloads_for_merge(
    person: PersonQuery,
    payloads: list[dict],
    synth_template,
    config: Config,
) -> list[dict]:
    budget = max(2000, _effective_request_limit(config) - 2600)
    fitted = [_compact_extraction_payload(payload) for payload in payloads]

    while fitted:
        prompt = synth_template.render(person=person, extracted_batches=fitted)
        tokens = _token_count(prompt)
        if tokens <= budget:
            logger.info(
                f"Person-model merge payload fitted to {tokens:,} tokens "
                f"across {len(fitted)} compact batches"
            )
            return fitted

        logger.warning(
            f"Person-model merge payload is {tokens:,} tokens "
            f"(budget {budget:,}); trimming extracted batches"
        )

        if len(fitted) > 8:
            fitted = fitted[: max(8, len(fitted) // 2)]
            continue

        for payload in fitted:
            payload["observations"] = payload["observations"][: max(1, len(payload["observations"]) - 1)]
            payload["timeline"] = payload["timeline"][:1]
            payload["success_hypotheses"] = payload["success_hypotheses"][:1]

        if all(len(payload["observations"]) <= 1 for payload in fitted):
            fitted = fitted[: max(1, len(fitted) // 2)]

    return []


def _fit_chunk_for_extraction(
    person: PersonQuery,
    chunk: Chunk,
    chunk_index: int,
    chunk_template,
    config: Config,
) -> str:
    request_limit = _effective_request_limit(config)
    budget = max(1800, request_limit - 1700)
    content = chunk.content

    while True:
        prompt = chunk_template.render(
            person=person,
            chunk={
                "source": chunk.source,
                "url": chunk.url,
                "content": content,
            },
            chunk_index=chunk_index,
        )
        tokens = _token_count(prompt)
        if tokens <= budget:
            if content != chunk.content:
                logger.warning(
                    f"Trimmed extraction chunk {chunk_index} from {chunk.token_count:,} tokens "
                    f"to fit {tokens:,}-token prompt budget"
                )
            return prompt

        current_tokens = _token_count(content)
        if current_tokens <= 400:
            logger.warning(
                f"Extraction chunk {chunk_index} still large at {tokens:,} prompt tokens; "
                "sending aggressively truncated content"
            )
            return prompt

        reduced_tokens = max(300, int(current_tokens * 0.7))
        head_budget = max(150, reduced_tokens // 2)
        tail_budget = max(100, reduced_tokens - head_budget)
        head = _truncate_to_tokens(content, head_budget)
        tail = _truncate_to_tokens(content[::-1], tail_budget)[::-1]
        content = (
            f"{head}\n\n[... content truncated for request budget ...]\n\n{tail}"
            if tail and tail not in head
            else head
        )


def _compress_tweets(
    twitter_chunks: list[Chunk], person: PersonQuery, config: Config
) -> list[Chunk]:
    """
    If total tweet tokens exceed the safe per-call budget, summarize them in
    batches small enough to fit under groq_tpm_limit, then return synthetic
    summary chunks in their place.
    """
    total = sum(c.token_count for c in twitter_chunks)
    # Reserve ~8K tokens for the profile template + output; tweets get the rest
    safe_tweet_budget = max(1000, _effective_request_limit(config) - 1500)

    if total <= safe_tweet_budget:
        return twitter_chunks  # fits fine, no summarisation needed

    logger.info(
        f"Tweet content is {total:,} tokens (budget {safe_tweet_budget:,}). "
        f"Pre-summarising {len(twitter_chunks)} tweet batches…"
    )

    client = _make_client(config)
    batch_token_limit = safe_tweet_budget  # each summary call stays under limit
    current_batch_chunks: list[Chunk] = []
    current_batch_tokens = 0
    summaries: list[Chunk] = []
    batch_num = 0

    def _flush(batch: list[Chunk], bnum: int) -> Chunk:
        combined_text = "\n".join(c.content for c in batch)
        tweet_lines = [l for l in combined_text.splitlines() if l.strip()]
        combined_text, fitted_count = _fit_tweet_batch_for_summary(
            combined_text,
            len(tweet_lines),
            person,
            config,
        )
        logger.info(
            f"  Summarising tweet batch {bnum + 1} ({fitted_count} tweets after fitting)…"
        )
        summary_text = _summarize_tweet_batch(
            combined_text, fitted_count, person, client, config.groq_model
        )
        return Chunk(
            source="twitter",
            url=batch[0].url,
            content=f"[Tweet digest — batch {bnum + 1}]\n{summary_text}",
            token_count=_token_count(summary_text),
            metadata={"summarised": True, "batch": bnum},
            is_summary=True,
        )

    for chunk in twitter_chunks:
        if current_batch_tokens + chunk.token_count > batch_token_limit and current_batch_chunks:
            summaries.append(_flush(current_batch_chunks, batch_num))
            batch_num += 1
            current_batch_chunks = []
            current_batch_tokens = 0
            time.sleep(2)  # brief pause between Groq calls

        current_batch_chunks.append(chunk)
        current_batch_tokens += chunk.token_count

    if current_batch_chunks:
        summaries.append(_flush(current_batch_chunks, batch_num))

    compressed = sum(c.token_count for c in summaries)
    logger.info(
        f"Tweet pre-summarisation done: {total:,} → {compressed:,} tokens "
        f"({len(summaries)} digest chunks)"
    )
    return summaries


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def synthesize_profile(
    person: PersonQuery,
    chunks: list[Chunk],
    person_model: PersonModel,
    config: Config,
    generated_at: str,
) -> str:
    env = _get_jinja_env()
    template = env.get_template("profile.j2")
    client = _make_client(config)

    twitter_chunks = [c for c in chunks if c.source == "twitter"]
    other_chunks = [c for c in chunks if c.source != "twitter"]

    # Pre-summarise tweets if they'd blow the TPM budget
    twitter_chunks = _compress_tweets(twitter_chunks, person, config)

    final_chunks = twitter_chunks + other_chunks

    prompt = template.render(
        person=person,
        generated_at=generated_at,
        person_model=person_model,
        twitter_content=twitter_chunks or None,
        web_content=[c for c in other_chunks if c.source == "web"] or None,
        github_content=[c for c in other_chunks if c.source == "github"] or None,
        linkedin_content=[c for c in other_chunks if c.source == "linkedin"] or None,
    )

    total_tokens = _token_count(prompt)
    request_limit = _effective_request_limit(config)
    logger.info(
        f"Calling LLM ({config.groq_model}) with {len(final_chunks)} chunks, "
        f"{total_tokens:,} prompt tokens (limit {request_limit:,})…"
    )

    if total_tokens > request_limit:
        logger.warning(
            f"Prompt is still {total_tokens:,} tokens after compression "
            f"(limit {request_limit:,}). Truncating tweet content."
        )
        # Hard truncate: keep only as many tweet chunks as fit
        kept: list[Chunk] = []
        budget = request_limit - _token_count(
            template.render(
                person=person, generated_at=generated_at,
                person_model=PersonModel(),
                twitter_content=None, web_content=None,
                github_content=None, linkedin_content=None,
            )
        ) - 500  # safety margin
        for c in twitter_chunks:
            if budget - c.token_count < 0:
                break
            kept.append(c)
            budget -= c.token_count
        prompt = template.render(
            person=person,
            generated_at=generated_at,
            person_model=person_model,
            twitter_content=kept or None,
            web_content=[c for c in other_chunks if c.source == "web"] or None,
            github_content=[c for c in other_chunks if c.source == "github"] or None,
            linkedin_content=[c for c in other_chunks if c.source == "linkedin"] or None,
        )
        logger.info(f"Truncated to {_token_count(prompt):,} tokens")

    return _call_llm(client, config.groq_model, prompt, max_tokens=4096)


def build_person_model(
    person: PersonQuery,
    chunks: list[Chunk],
    config: Config,
) -> PersonModel:
    env = _get_jinja_env()
    chunk_template = env.get_template("extract_observations.j2")
    synth_template = env.get_template("person_model.j2")
    client = _make_client(config)

    observation_payloads: list[dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        prompt = _fit_chunk_for_extraction(
            person=person,
            chunk=chunk,
            chunk_index=idx,
            chunk_template=chunk_template,
            config=config,
        )
        logger.info(
            f"Extracting structured observations from chunk {idx}/{len(chunks)} "
            f"({chunk.source}, {chunk.token_count:,} tokens, prompt {_token_count(prompt):,} tokens)"
        )
        raw = _call_llm(client, config.groq_model, prompt, max_tokens=1400)
        parsed = _parse_json_object(raw)
        if parsed:
            observation_payloads.append(parsed)

    fitted_payloads = _fit_extraction_payloads_for_merge(
        person,
        observation_payloads,
        synth_template,
        config,
    )
    synthesis_prompt = synth_template.render(
        person=person,
        extracted_batches=fitted_payloads,
    )
    raw_model = _call_llm(client, config.groq_model, synthesis_prompt, max_tokens=2200)
    parsed_model = _parse_json_object(raw_model)
    if not parsed_model:
        logger.warning("Structured person model synthesis returned no parseable JSON")
        return PersonModel()

    try:
        return PersonModel.model_validate(_normalize_person_model_payload(parsed_model))
    except Exception as exc:
        logger.warning(f"Structured person model validation failed: {exc}")
        return PersonModel()


def summarize_chunk(chunk: Chunk, person: PersonQuery, config: Config) -> str:
    env = _get_jinja_env()
    template = env.get_template("summarize.j2")
    prompt = template.render(
        person_name=person.name,
        source=chunk.source,
        url=chunk.url,
        content=chunk.content,
    )
    return _call_llm(_make_client(config), config.groq_model, prompt, max_tokens=1024)


def answer_question(
    question: str,
    person: PersonQuery,
    chunks: list[Chunk],
    person_model: PersonModel,
    config: Config,
) -> str:
    env = _get_jinja_env()
    template = env.get_template("ask.j2")
    prompt = template.render(
        person_name=person.name,
        question=question,
        chunks=chunks,
        person_model=person_model,
    )
    return _call_llm(_make_client(config), config.groq_model, prompt, max_tokens=2048)
