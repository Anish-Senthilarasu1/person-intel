from __future__ import annotations

from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.theme import Theme
from rich.text import Text

from person_intel.config import Config
from person_intel.pipeline import run
from person_intel.storage.models import PersonModel, PersonQuery

console = Console(
    theme=Theme(
        {
            "brand": "bold bright_cyan",
            "accent": "bright_magenta",
            "muted": "dim cyan",
            "ok": "bold bright_green",
            "warn": "bold yellow",
            "panel.border": "bright_blue",
            "table.header": "bold bright_cyan",
        }
    )
)

BANNER = r"""
       ____                                ____      __       __
      / __ \___  ______________  ____     /  _/___  / /____  / /
     / /_/ / _ \/ ___/ ___/ __ \/ __ \    / // __ \/ __/ _ \/ /
    / ____/  __/ /  (__  ) /_/ / / / /  _/ // / / / /_/  __/ /
   /_/    \___/_/  /____/\____/_/ /_/  /___/_/ /_/\__/\___/_/
"""

SOURCE_COLORS = {
    "twitter": "bright_cyan",
    "web": "bright_green",
    "linkedin": "bright_blue",
}


def _render_banner() -> None:
    art = Text(BANNER.strip("\n"), style="brand")
    subtitle = Text("public traces -> structured markdown intelligence", style="muted")
    console.print(
        Panel(
            Align.center(Group(art, subtitle)),
            border_style="panel.border",
            box=box.DOUBLE,
            padding=(1, 2),
            expand=False,
        )
    )


def _source_badges(sources: list[str]) -> Text:
    text = Text()
    for index, source in enumerate(sources):
        if index:
            text.append("  ")
        color = SOURCE_COLORS.get(source, "bright_white")
        text.append(f" {source.upper()} ", style=f"bold black on {color}")
    return text


def _settings_table(person: PersonQuery, sources: list[str], config: Config, build_rag: bool, llm: bool) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="muted", justify="right")
    table.add_column(style="bright_white")
    table.add_row("person", person.name)
    table.add_row("sources", _source_badges(sources))
    table.add_row("tweets", str(config.max_tweets))
    table.add_row("web pages", str(config.max_web_pages))
    table.add_row("mode", "LLM synthesis" if llm else "raw markdown dump")
    table.add_row("Q&A index", "enabled" if build_rag else "disabled")
    return table


def _panel(renderable, title: str, border_style: str = "panel.border") -> Panel:
    return Panel(
        renderable,
        title=f"[bold]{title}[/bold]",
        border_style=border_style,
        box=box.ROUNDED,
        padding=(1, 2),
        expand=False,
    )


def _load_config() -> Config:
    return Config()


def _strip_frontmatter(markdown: str) -> str:
    lines = markdown.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            return "\n".join(lines[end + 1:]).strip()
        except ValueError:
            return markdown.strip()
    return markdown.strip()


def _preview_markdown(path: Path, max_lines: int = 80) -> None:
    text = _strip_frontmatter(path.read_text())
    preview = "\n".join(text.splitlines()[:max_lines]).strip()
    console.print(_panel(Markdown(preview or "_No content_"), "Profile Preview"))


def _load_person_model(output_dir: Path, person: PersonQuery) -> PersonModel:
    path = output_dir / person.slug / "person_model.json"
    if not path.exists():
        return PersonModel()
    return PersonModel.model_validate_json(path.read_text())


def _has_person_model(output_dir: Path, person: PersonQuery) -> bool:
    return (output_dir / person.slug / "person_model.json").exists()


def _render_model_summary(person_model: PersonModel) -> None:
    table = Table(
        title="Structured Person Model",
        show_header=True,
        header_style="table.header",
        border_style="panel.border",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Observations", str(len(person_model.observations)))
    table.add_row("Timeline events", str(len(person_model.timeline)))
    table.add_row("Success hypotheses", str(len(person_model.success_hypotheses)))
    table.add_row("Contradictions", str(len(person_model.contradictions)))
    table.add_row("Open questions", str(len(person_model.open_questions)))
    console.print(table)

    if person_model.success_hypotheses:
        hyp_table = Table(
            title="Top Success Hypotheses",
            show_header=True,
            header_style="bold bright_green",
            border_style="bright_green",
            box=box.SIMPLE_HEAVY,
        )
        hyp_table.add_column("Title")
        hyp_table.add_column("Confidence", justify="right")
        hyp_table.add_column("Mechanism")
        for hypothesis in person_model.success_hypotheses[:5]:
            hyp_table.add_row(
                hypothesis.title,
                f"{hypothesis.confidence:.2f}",
                hypothesis.mechanism,
            )
        console.print(hyp_table)


def _prompt_enabled_sources(config: Config) -> list[str]:
    source_defaults = {
        "twitter": bool(config.socialdata_api_key or config.twitterapi_io_key),
        "web": True,
        "linkedin": True,
    }

    sources: list[str] = []
    for source, default in source_defaults.items():
        label = source
        if source == "twitter" and not default:
            label = "twitter (disabled by default: no API key configured)"
        if Confirm.ask(f"Use {label}?", default=default):
            sources.append(source)

    if not sources:
        console.print("[warn]No sources selected. Falling back to web.[/warn]")
        return ["web"]
    return sources


def _prompt_person(config: Config) -> tuple[PersonQuery, list[str], bool, bool]:
    _render_banner()
    console.print(
        _panel(
            Text.from_markup(
                "[brand]Terminal workflow[/brand]\n"
                "Build a single markdown intelligence file without the web app.\n\n"
                "[muted]Twitter is API-only. LinkedIn stays public-web only for now; no bypass flow.[/muted]"
            ),
            "Person Intel",
        )
    )
    console.print(Rule(style="muted"))

    name = Prompt.ask("Full name").strip()
    twitter = Prompt.ask("Twitter handle", default="").strip() or None
    linkedin = Prompt.ask("LinkedIn slug", default="").strip() or None
    company = Prompt.ask("Company / org", default="").strip() or None

    sources = _prompt_enabled_sources(config)
    max_tweets = IntPrompt.ask("Max tweets", default=config.max_tweets)
    max_pages = IntPrompt.ask("Max web pages", default=config.max_web_pages)
    llm = Confirm.ask("Use LLM synthesis?", default=False)
    build_rag = Confirm.ask("Build Q&A index?", default=False)

    config.max_tweets = max_tweets
    config.max_web_pages = max_pages

    person = PersonQuery(
        name=name,
        company=company,
        twitter_handle=twitter,
        linkedin_slug=linkedin,
    )
    return person, sources, build_rag, llm


def _show_run_summary(output_file: Path, person: PersonQuery, config: Config) -> None:
    console.print(
        _panel(
            Text.from_markup(
                f"[ok]Profile saved[/ok]\n{output_file}\n\n"
                f"[bold]Person[/bold]: {person.name}"
            ),
            "Run Complete",
            border_style="bright_green",
        )
    )
    if _has_person_model(config.output_dir, person):
        _render_model_summary(_load_person_model(config.output_dir, person))
    _preview_markdown(output_file)


def _ask_loop(person: PersonQuery, config: Config) -> None:
    from person_intel.rag.index import query_index
    from person_intel.synthesis.llm import answer_question

    person_model = _load_person_model(config.output_dir, person)
    console.print(
        "\n[brand]Q&A mode[/brand]  [muted]Enter a question about the person model. "
        "Press Enter on a blank line to leave.[/muted]"
    )
    while True:
        question = Prompt.ask("Question", default="").strip()
        if not question:
            return
        chunks = query_index(person, question, config.output_dir)
        if not chunks:
            console.print("[warn]No RAG index found for this person. Re-run with Q&A enabled.[/warn]")
            return
        answer = answer_question(question, person, chunks, person_model, config)
        console.print(_panel(Markdown(answer), question, border_style="accent"))


def main() -> None:
    while True:
        try:
            config = _load_config()
        except Exception as exc:
            console.print(f"[red]Config error:[/red] {exc}")
            return

        person, sources, build_rag, llm = _prompt_person(config)

        console.print(
            _panel(
                _settings_table(person, sources, config, build_rag, llm),
                person.name,
                border_style="accent",
            )
        )

        output_file = run(
            person=person,
            config=config,
            enabled_sources=sources,
            build_rag=build_rag,
            no_cache=False,
            raw_dump=not llm,
        )
        _show_run_summary(output_file, person, config)

        while True:
            action = Prompt.ask(
                "Next action",
                choices=["ask", "open", "new", "quit"],
                default="ask" if build_rag and llm else "new",
            )
            if action == "ask":
                if not llm:
                    console.print("[warn]Q&A uses the LLM path. Re-run with LLM synthesis enabled if you want that workflow.[/warn]")
                    continue
                _ask_loop(person, config)
            elif action == "open":
                _preview_markdown(output_file, max_lines=160)
            elif action == "new":
                console.print()
                break
            else:
                return


if __name__ == "__main__":
    main()
