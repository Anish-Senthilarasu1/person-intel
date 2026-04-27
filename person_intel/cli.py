from pathlib import Path
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from person_intel.config import Config
from person_intel.storage.models import PersonQuery

app = typer.Typer(
    name="person-intel",
    help="Collect a person's public traces into a single markdown intelligence file.",
    add_completion=False,
)
console = Console()

ALL_SOURCES = ["twitter", "web", "linkedin"]


def _load_config() -> Config:
    try:
        return Config()
    except Exception as e:
        console.print(f"[red]Config error:[/red] {e}")
        console.print("Copy .env.example to .env and fill in ANTHROPIC_API_KEY")
        raise typer.Exit(1)


@app.command()
def profile(
    name: Annotated[str, typer.Argument(help="Full name of the person to profile")],
    company: Annotated[Optional[str], typer.Option(help="Company or organization")] = None,
    twitter: Annotated[Optional[str], typer.Option(help="Twitter handle (no @)")] = None,
    linkedin: Annotated[Optional[str], typer.Option(help="LinkedIn profile slug")] = None,
    sources: Annotated[
        Optional[str],
        typer.Option(help="Comma-separated sources: twitter,web,linkedin"),
    ] = None,
    max_tweets: Annotated[Optional[int], typer.Option(help="Max tweets to fetch")] = None,
    max_pages: Annotated[Optional[int], typer.Option(help="Max web pages to fetch")] = None,
    since: Annotated[Optional[int], typer.Option(help="Only include content from this year onwards")] = None,
    llm: Annotated[bool, typer.Option("--llm", help="Enable LLM synthesis instead of raw markdown export")] = False,
    rag: Annotated[bool, typer.Option("--rag", help="Build ChromaDB RAG index")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass disk cache")] = False,
    output_dir: Annotated[Optional[Path], typer.Option(help="Output directory")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Export a person's scraped public data into a single markdown file."""
    if not verbose:
        logger.remove()
        logger.add(lambda msg: None)  # suppress logs unless verbose

    config = _load_config()

    # Apply CLI overrides
    if max_tweets is not None:
        config.max_tweets = max_tweets
    if max_pages is not None:
        config.max_web_pages = max_pages
    if output_dir is not None:
        config.output_dir = output_dir

    person = PersonQuery(
        name=name,
        company=company,
        twitter_handle=twitter,
        linkedin_slug=linkedin,
        since_year=since,
    )

    enabled_sources = ALL_SOURCES
    if sources:
        enabled_sources = [s.strip() for s in sources.split(",") if s.strip() in ALL_SOURCES]
        invalid = [s.strip() for s in sources.split(",") if s.strip() not in ALL_SOURCES]
        if invalid:
            console.print(f"[yellow]Warning: unknown sources ignored: {invalid}[/yellow]")

    console.print(
        Panel(
            Text.from_markup(
                f"[bold]Person:[/bold] {name}\n"
                f"[bold]Sources:[/bold] {', '.join(enabled_sources)}\n"
                f"[bold]Max tweets:[/bold] {config.max_tweets}  "
                f"[bold]Max pages:[/bold] {config.max_web_pages}\n"
                f"[bold]Mode:[/bold] {'llm synthesis' if llm else 'raw markdown dump'}\n"
                f"[bold]RAG:[/bold] {'yes' if rag else 'no'}  "
                f"[bold]Cache:[/bold] {'disabled' if no_cache else 'enabled'}"
            ),
            title="[bold blue]Person Intel[/bold blue]",
            expand=False,
        )
    )

    from person_intel.pipeline import run

    output_file = run(
        person=person,
        config=config,
        enabled_sources=enabled_sources,
        build_rag=rag,
        no_cache=no_cache,
        raw_dump=not llm,
    )

    console.print(
        Panel(
            f"[green]Profile saved:[/green] {output_file}",
            title="Done",
            expand=False,
        )
    )


@app.command()
def ask(
    name: Annotated[str, typer.Argument(help="Name of the person")],
    question: Annotated[str, typer.Argument(help="Question to answer")],
    output_dir: Annotated[Optional[Path], typer.Option(help="Output directory")] = None,
) -> None:
    """Ask a follow-up question about a profiled person (requires --rag)."""
    config = _load_config()
    if output_dir:
        config.output_dir = output_dir

    person = PersonQuery(name=name)

    from person_intel.rag.index import query_index
    from person_intel.storage.models import PersonModel
    from person_intel.synthesis.llm import answer_question

    chunks = query_index(person, question, config.output_dir)
    if not chunks:
        console.print("[red]No RAG index found. Run 'profile --rag' first.[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Retrieved {len(chunks)} relevant chunks...[/dim]\n")
    person_model_path = config.output_dir / person.slug / "person_model.json"
    if person_model_path.exists():
        person_model = PersonModel.model_validate_json(person_model_path.read_text())
    else:
        person_model = PersonModel()
    answer = answer_question(question, person, chunks, person_model, config)
    console.print(Panel(answer, title=f"Answer: {question}", expand=False))


@app.command("add-twitter-account")
def add_twitter_account(
    username: Annotated[str, typer.Option(help="Twitter username")],
    password: Annotated[str, typer.Option(help="Twitter password")],
    email: Annotated[str, typer.Option(help="Twitter account email")],
) -> None:
    """Add a Twitter account to the twscrape pool."""
    import asyncio

    config = _load_config()

    async def _add() -> None:
        try:
            import twscrape
        except ImportError:
            console.print("[red]twscrape not installed.[/red] Run: pip install twscrape")
            raise typer.Exit(1)

        config.twscrape_accounts_db.parent.mkdir(parents=True, exist_ok=True)
        api = twscrape.API(str(config.twscrape_accounts_db))
        await api.pool.add_account(username, password, email, password)
        await api.pool.login_all()
        console.print(f"[green]Account @{username} added and logged in.[/green]")

    asyncio.run(_add())


if __name__ == "__main__":
    app()
