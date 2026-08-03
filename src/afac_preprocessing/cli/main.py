"""afac-preprocess, CLI for the preprocessing pipeline.

The only place in the repo allowed to do ``sys.exit`` (via ``typer.Exit``)
and to configure logging. No business logic here: the CLI builds
``Settings``, one ``PipelineContext`` per PDF, calls ``Pipeline``, prints
the report, and converts business exceptions into stable exit codes:

    0  success
    1  failed step / business error
    2  invalid configuration (ConfigError)
    3  unknown step or profile (UnknownStep)
    4  VLM/embedding unavailable
"""

from __future__ import annotations

import csv
import importlib.metadata
import platform
from pathlib import Path

import typer

from ..aggregate import aggregate_all_roots, aggregate_root_csv
from ..clients.bundle import ClientBundle
from ..context import PipelineContext
from ..core.pipeline import Pipeline
from ..core.registry import PROFILES
from ..exceptions import AfacError, ConfigError, UnknownStep, VlmUnavailable
from ..logging_config import configure_logging
from ..settings import Settings

app = typer.Typer(
    name="afac-preprocess",
    help="AFAC preprocessing pipeline: Docling extraction, VLM enrichment, metadata.",
    no_args_is_help=True,
    add_completion=False,
)


def _exit_code(exc: AfacError) -> int:
    if isinstance(exc, ConfigError):
        return 2
    if isinstance(exc, UnknownStep):
        return 3
    if isinstance(exc, VlmUnavailable):
        return 4
    return 1


def _resolve_dotenv(dotenv: Path | None) -> Path | None:
    """Explicit --dotenv, otherwise .env then .env.test in the current directory."""
    if dotenv is not None:
        return dotenv
    for candidate in (Path(".env"), Path(".env.test")):
        if candidate.exists():
            return candidate
    return None


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _build_pipeline(
    *,
    profile: str,
    from_step: str | None,
    to_step: str | None,
    skip: str,
    only: str,
    with_opencv_check: bool,
) -> Pipeline:
    if profile not in PROFILES:
        raise UnknownStep(
            f"Unknown profile {profile!r}. Valid profiles: {', '.join(sorted(PROFILES))}"
        )
    params = dict(PROFILES[profile])

    only_refs = _csv(only)
    if only_refs:
        return Pipeline.default().select(only=only_refs)

    skip_refs = list(params.get("skip", [])) + _csv(skip)  # type: ignore[arg-type]
    return Pipeline.default().select(
        from_=from_step if from_step is not None else None,
        to=to_step if to_step is not None else params.get("to"),  # type: ignore[arg-type]
        skip=skip_refs,
        include_disabled=bool(params.get("include_disabled")) or with_opencv_check,
    )


def _discover_pdfs(input_path: Path) -> list[Path]:
    """A single PDF, or a directory explored recursively (current behavior preserved)."""
    resolved = input_path.resolve()
    if not resolved.exists():
        raise ConfigError(f"Input not found: {resolved}")
    if resolved.is_dir():
        pdfs = sorted(resolved.rglob("*.pdf"))
        if not pdfs:
            raise ConfigError(f"No PDF files found under {resolved}")
        return pdfs
    return [resolved]


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", help="PDF or directory (explored recursively)."),
    dotenv: Path | None = typer.Option(None, help="Env file (default: .env then .env.test)."),
    profile: str = typer.Option("default", help=f"Profile: {', '.join(sorted(PROFILES))}."),
    from_step: str | None = typer.Option(None, "--from-step", help="First step (name or number)."),
    to_step: str | None = typer.Option(None, "--to-step", help="Last step (name or number)."),
    skip: str = typer.Option("", "--skip", help="Steps to skip (names/numbers, comma-separated)."),
    only: str = typer.Option("", "--only", help="Run only these steps (takes precedence over from/to/skip)."),
    no_ocr: bool = typer.Option(False, "--no-ocr", help="Passed to docling-extract only."),
    with_opencv_check: bool = typer.Option(False, "--with-opencv-check", help="Include the opencv-check step."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the steps without running them."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v = DEBUG."),
) -> None:
    """Processes a PDF or all the PDFs in a directory through the pipeline's 13 steps."""
    configure_logging(verbose)
    try:
        settings = Settings.from_dotenv(_resolve_dotenv(dotenv))
        pdfs = _discover_pdfs(input)
        pipeline = _build_pipeline(
            profile=profile, from_step=from_step, to_step=to_step,
            skip=skip, only=only, with_opencv_check=with_opencv_check,
        )
        if not pipeline.steps:
            raise ConfigError("No steps selected, check --from-step/--to-step/--skip/--only.")
        if no_ocr:
            for step in pipeline.steps:
                if step.name == "docling-extract" and hasattr(step, "ocr"):
                    step.ocr = False

        typer.echo(f"{len(pdfs)} PDF(s) — steps: {', '.join(s.name for s in pipeline.steps)}")
        if dry_run:
            # No VLM client, no aggregation: a dry-run must touch NEITHER the
            # network NOR the disk. Without this short-circuit, ClientBundle
            # probes the VLM and run_batch rewrites the global CSVs.
            for pdf in pdfs:
                typer.echo(f"  · {pdf}")
            typer.echo("--dry-run: no step executed, no file written.")
            raise typer.Exit(0)
        with ClientBundle(settings) as bundle:
            contexts = (
                PipelineContext.for_pdf(pdf, settings, clients=bundle, dry_run=dry_run)
                for pdf in pdfs
            )
            batch = pipeline.run_batch(contexts)

        for report in batch.reports:
            mark = "✓" if report.ok else "✗"
            typer.echo(f"  {mark} {report.doc_name} — {len(report.results)} step(s), {report.duration:.1f}s")
            if not report.ok:
                for name, result in report.results:
                    if not result.ok:
                        typer.echo(f"      failed step: {name} — {result.message}")
        for csv_path in batch.aggregated:
            typer.echo(f"  Global CSV: {csv_path}")
        typer.echo(
            f"{'OK' if batch.ok else 'FAILED'} — {len(batch.reports)} document(s), "
            f"{len(batch.failed)} failure(s)"
        )
        raise typer.Exit(0 if batch.ok else 1)
    except AfacError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(_exit_code(exc)) from exc


@app.command()
def aggregate(
    root: str | None = typer.Option(
        None, help="Root directory to aggregate (e.g. afac). Default: all roots."
    ),
    dotenv: Path | None = typer.Option(None, help="Env file (default: .env then .env.test)."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v = DEBUG."),
) -> None:
    """Rebuilds the global CSV for each root directory (<root>/<root>.csv).

    Concatenates the per-document CSVs of the subtree, without modifying
    them. Replayable: the file is rebuilt, never appended to.
    """
    configure_logging(verbose)
    try:
        settings = Settings.from_dotenv(_resolve_dotenv(dotenv))
        out_root = settings.output_files_root
        if root:
            written = [aggregate_root_csv(out_root, root)]
        else:
            written = aggregate_all_roots(out_root)
        if not written:
            typer.echo(f"No per-document CSV found under {out_root}")
            raise typer.Exit(0)
        for path in written:
            with path.open(newline="", encoding="utf-8") as fh:
                n_rows = sum(1 for _ in csv.reader(fh)) - 1
            typer.echo(f"  ✓ {path} — {n_rows} row(s)")
        raise typer.Exit(0)
    except AfacError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(_exit_code(exc)) from exc


@app.command()
def steps(
    graph: bool = typer.Option(False, "--graph", help="Show the inputs ← outputs chaining."),
) -> None:
    """Lists the 13 steps (works without .env)."""
    pipeline = Pipeline.default()
    if not graph:
        typer.echo(f"{'#':>2}  {'Name':<26} {'VLM':<4} {'Default':<7} Description")
        for i, step in enumerate(pipeline.steps, start=1):
            vlm = "yes" if step.requires_vlm else "—"
            default = "yes" if step.enabled_by_default else "no"
            typer.echo(f"{i:02d}  {step.name:<26} {vlm:<4} {default:<7} {step.description}")
        return

    # Chaining deduced from inputs()/outputs() (batch 4) — no .env required:
    # synthetic Settings, fake paths, we're only comparing Path objects.
    settings = Settings(
        vlm_url="http://steps-graph.invalid/v1",  # type: ignore[arg-type]
        project_root=Path("/steps-graph"),
        data_root=Path("/steps-graph/data"),
    )
    ctx = PipelineContext.for_pdf(Path("/steps-graph/doc.pdf"), settings)
    producers: dict[Path, str] = {}
    typer.echo("Step chaining (input ← produced by):")
    for step in pipeline.steps:
        needs = []
        for path in step.inputs(ctx):
            if path == ctx.workspace.source_pdf:
                needs.append("PDF source")
            elif path in producers:
                needs.append(f"{path.name} ← {producers[path]}")
            else:
                needs.append(f"{path.name} ← ???")
        for path in step.outputs(ctx):
            producers.setdefault(path, step.name)
        joined = "; ".join(needs) if needs else "—"
        typer.echo(f"  {step.name:<26} {joined}")
    ctx.clients.close()


@app.command()
def doctor(
    dotenv: Path | None = typer.Option(None, help="Env file to diagnose."),
) -> None:
    """Diagnoses the installation and says what to do for each problem."""
    import httpx

    from ..clients.openai_client import _to_base_url

    failures = 0

    def ok(msg: str) -> None:
        typer.echo(f"  ✓ {msg}")

    def warn(msg: str, advice: str) -> None:
        typer.echo(f"  ⚠ {msg}\n    → {advice}")

    def fail(msg: str, advice: str) -> None:
        nonlocal failures
        failures += 1
        typer.echo(f"  ✗ {msg}\n    → {advice}")

    typer.echo("afac-preprocess doctor")

    # 1. Python
    if platform.python_version_tuple() >= ("3", "11"):
        ok(f"Python {platform.python_version()}")
    else:
        fail(f"Python {platform.python_version()} < 3.11", "Install Python ≥ 3.11 (uv python install 3.12).")

    # 2. .env
    resolved = _resolve_dotenv(dotenv)
    if resolved is None:
        warn("no .env file found (.env, .env.test)",
             "cp .env.example .env then fill in VLM_URL (see the infra team).")
    else:
        ok(f".env: {resolved}")

    # 3. Settings
    try:
        settings = Settings.from_dotenv(resolved)
    except ConfigError as exc:
        fail(f"invalid configuration: {exc}",
             "Fill in VLM_URL (and EMBEDDING_URL for the metadata/hyq steps) in the .env.")
        raise typer.Exit(1) from exc
    ok(f"VLM_URL: {settings.vlm_url} (model: {settings.vlm_model_name or 'not set'})")

    # 4. Certificate
    if settings.ca_pem is not None and settings.ca_pem.exists():
        ok(f"CA certificate: {settings.ca_pem}")
    elif settings.ca_pem is not None:
        warn(f"VLM_CA_PEM not found ({settings.ca_pem}) — falling back to certifi",
             "Check the certificate path provided by the infra team.")
    else:
        ok("certificate: certifi (no custom CA declared)")

    # 5. VLM reachable + model served
    base = _to_base_url(str(settings.vlm_url))
    try:
        response = httpx.Client(verify=settings.resolved_ca_path, timeout=5.0).get(f"{base}/models")
        response.raise_for_status()
        models = [m.get("id", "?") for m in response.json().get("data", [])]
        ok(f"VLM reachable — models served: {models or ['(none)']}")
        if settings.vlm_model_name and settings.vlm_model_name not in models:
            fail(f"the configured model '{settings.vlm_model_name}' is not served",
                 f"Choose one of the models listed above, or check the backend ({base}).")
    except Exception as exc:
        fail(f"VLM unreachable ({base}): {type(exc).__name__}",
             "Check VLM_URL, the VPN/network, and the certificate (VLM_CA_PEM).")

    # 6. Embedding (optional)
    if settings.embedding_url is None:
        warn("EMBEDDING_URL not set",
             "The metadata-generation and hyq-embedding steps will fail; set EMBEDDING_URL to enable them.")
    else:
        ok(f"EMBEDDING_URL: {settings.embedding_url}")

    # 7. data/
    if settings.input_files_root.is_dir():
        pdf_count = sum(1 for _ in settings.input_files_root.rglob("*.pdf"))
        ok(f"input_files: {settings.input_files_root} ({pdf_count} PDF)")
    else:
        fail(f"input directory missing: {settings.input_files_root}",
             "mkdir -p data/input_files then drop the PDFs there (or override DATA_ROOT).")
    try:
        settings.output_files_root.mkdir(parents=True, exist_ok=True)
        probe = settings.output_files_root / ".doctor-write-check"
        probe.write_text("ok")
        probe.unlink()
        ok(f"outputs writable: {settings.output_files_root}")
    except OSError as exc:
        fail(f"outputs not writable ({settings.output_files_root}): {exc}",
             "Check the write permissions or override DATA_ROOT.")

    typer.echo("Everything is ready." if failures == 0 else f"{failures} problem(s) to fix.")
    raise typer.Exit(0 if failures == 0 else 1)


@app.command()
def version() -> None:
    """Displays the installed version."""
    typer.echo(importlib.metadata.version("afac-preprocessing"))


if __name__ == "__main__":
    app()
