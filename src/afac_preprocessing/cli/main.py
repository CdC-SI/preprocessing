"""afac-preprocess — CLI du pipeline de prétraitement.

Seul endroit du dépôt autorisé à faire ``sys.exit`` (via ``typer.Exit``) et à
configurer le logging. Aucune logique métier ici : la CLI construit
``Settings``, un ``PipelineContext`` par PDF, appelle ``Pipeline``, imprime le
rapport et convertit les exceptions métier en codes de sortie stables :

    0  succès
    1  étape en échec / erreur métier
    2  configuration invalide (ConfigError)
    3  étape ou profil inconnu (UnknownStep)
    4  VLM/embedding indisponible
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
    help="Pipeline de prétraitement AFAC : extraction Docling, enrichissement VLM, metadata.",
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
    """--dotenv explicite sinon .env puis .env.test du répertoire courant."""
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
    """Un PDF, ou un dossier exploré récursivement (comportement actuel préservé)."""
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
    input: Path = typer.Option(..., "--input", "-i", help="PDF ou dossier (exploré récursivement)."),
    dotenv: Path | None = typer.Option(None, help="Fichier .env (défaut : .env puis .env.test)."),
    profile: str = typer.Option("default", help=f"Profil : {', '.join(sorted(PROFILES))}."),
    from_step: str | None = typer.Option(None, "--from-step", help="Première étape (nom ou numéro)."),
    to_step: str | None = typer.Option(None, "--to-step", help="Dernière étape (nom ou numéro)."),
    skip: str = typer.Option("", "--skip", help="Étapes à sauter (noms/numéros, séparés par des virgules)."),
    only: str = typer.Option("", "--only", help="N'exécuter que ces étapes (prime sur from/to/skip)."),
    no_ocr: bool = typer.Option(False, "--no-ocr", help="Transmis à docling-extract uniquement."),
    with_opencv_check: bool = typer.Option(False, "--with-opencv-check", help="Inclure l'étape opencv-check."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Afficher les étapes sans les exécuter."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v = DEBUG."),
) -> None:
    """Traite un PDF ou tous les PDF d'un dossier avec les 13 étapes du pipeline."""
    configure_logging(verbose)
    try:
        settings = Settings.from_dotenv(_resolve_dotenv(dotenv))
        pdfs = _discover_pdfs(input)
        pipeline = _build_pipeline(
            profile=profile, from_step=from_step, to_step=to_step,
            skip=skip, only=only, with_opencv_check=with_opencv_check,
        )
        if not pipeline.steps:
            raise ConfigError("No steps selected — check --from-step/--to-step/--skip/--only.")
        if no_ocr:
            for step in pipeline.steps:
                if step.name == "docling-extract" and hasattr(step, "ocr"):
                    step.ocr = False

        typer.echo(f"{len(pdfs)} PDF(s) — étapes : {', '.join(s.name for s in pipeline.steps)}")
        if dry_run:
            # Ni client VLM, ni agrégation : un dry-run ne doit toucher NI le
            # réseau NI le disque. Sans ce court-circuit, ClientBundle sonde le
            # VLM et run_batch réécrit les CSV globaux.
            for pdf in pdfs:
                typer.echo(f"  · {pdf}")
            typer.echo("--dry-run : aucune étape exécutée, aucun fichier écrit.")
            raise typer.Exit(0)
        with ClientBundle(settings) as bundle:
            contexts = (
                PipelineContext.for_pdf(pdf, settings, clients=bundle, dry_run=dry_run)
                for pdf in pdfs
            )
            batch = pipeline.run_batch(contexts)

        for report in batch.reports:
            mark = "✓" if report.ok else "✗"
            typer.echo(f"  {mark} {report.doc_name} — {len(report.results)} étape(s), {report.duration:.1f}s")
            if not report.ok:
                for name, result in report.results:
                    if not result.ok:
                        typer.echo(f"      étape en échec : {name} — {result.message}")
        for csv_path in batch.aggregated:
            typer.echo(f"  CSV global : {csv_path}")
        typer.echo(
            f"{'OK' if batch.ok else 'ÉCHEC'} — {len(batch.reports)} document(s), "
            f"{len(batch.failed)} échec(s)"
        )
        raise typer.Exit(0 if batch.ok else 1)
    except AfacError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(_exit_code(exc)) from exc


@app.command()
def aggregate(
    root: str | None = typer.Option(
        None, help="Dossier racine à agréger (ex. afac). Par défaut : toutes les racines."
    ),
    dotenv: Path | None = typer.Option(None, help="Fichier .env (défaut : .env puis .env.test)."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v = DEBUG."),
) -> None:
    """Reconstruit le CSV global de chaque dossier racine (<racine>/<racine>.csv).

    Concatène les CSV par document du sous-arbre, sans les modifier. Rejouable :
    le fichier est reconstruit, jamais complété.
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
            typer.echo(f"Aucun CSV par document trouvé sous {out_root}")
            raise typer.Exit(0)
        for path in written:
            with path.open(newline="", encoding="utf-8") as fh:
                n_rows = sum(1 for _ in csv.reader(fh)) - 1
            typer.echo(f"  ✓ {path} — {n_rows} ligne(s)")
        raise typer.Exit(0)
    except AfacError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(_exit_code(exc)) from exc


@app.command()
def steps(
    graph: bool = typer.Option(False, "--graph", help="Afficher le chaînage entrées ← sorties."),
) -> None:
    """Liste les 13 étapes (fonctionne sans .env)."""
    pipeline = Pipeline.default()
    if not graph:
        typer.echo(f"{'#':>2}  {'Nom':<26} {'VLM':<4} {'Défaut':<7} Description")
        for i, step in enumerate(pipeline.steps, start=1):
            vlm = "oui" if step.requires_vlm else "—"
            default = "oui" if step.enabled_by_default else "non"
            typer.echo(f"{i:02d}  {step.name:<26} {vlm:<4} {default:<7} {step.description}")
        return

    # Chaînage déduit de inputs()/outputs() (lot 4) — aucun .env requis :
    # Settings synthétiques, chemins fictifs, on ne fait que comparer des Path.
    settings = Settings(
        vlm_url="http://steps-graph.invalid/v1",  # type: ignore[arg-type]
        project_root=Path("/steps-graph"),
        data_root=Path("/steps-graph/data"),
    )
    ctx = PipelineContext.for_pdf(Path("/steps-graph/doc.pdf"), settings)
    producers: dict[Path, str] = {}
    typer.echo("Chaînage des étapes (entrée ← produite par) :")
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
    dotenv: Path | None = typer.Option(None, help="Fichier .env à diagnostiquer."),
) -> None:
    """Diagnostique l'installation et dit quoi faire pour chaque problème."""
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
        fail(f"Python {platform.python_version()} < 3.11", "Installer Python ≥ 3.11 (uv python install 3.12).")

    # 2. .env
    resolved = _resolve_dotenv(dotenv)
    if resolved is None:
        warn("aucun fichier .env trouvé (.env, .env.test)",
             "cp .env.example .env puis renseigner VLM_URL (voir l'équipe infra).")
    else:
        ok(f".env : {resolved}")

    # 3. Settings
    try:
        settings = Settings.from_dotenv(resolved)
    except ConfigError as exc:
        fail(f"configuration invalide : {exc}",
             "Renseigner VLM_URL (et EMBEDDING_URL pour les étapes metadata/hyq) dans le .env.")
        raise typer.Exit(1) from exc
    ok(f"VLM_URL : {settings.vlm_url} (modèle : {settings.vlm_model_name or 'non renseigné'})")

    # 4. Certificat
    if settings.ca_pem is not None and settings.ca_pem.exists():
        ok(f"certificat CA : {settings.ca_pem}")
    elif settings.ca_pem is not None:
        warn(f"VLM_CA_PEM introuvable ({settings.ca_pem}) — repli certifi",
             "Vérifier le chemin du certificat fourni par l'équipe infra.")
    else:
        ok("certificat : certifi (aucun CA custom déclaré)")

    # 5. VLM joignable + modèle servi
    base = _to_base_url(str(settings.vlm_url))
    try:
        response = httpx.Client(verify=settings.resolved_ca_path, timeout=5.0).get(f"{base}/models")
        response.raise_for_status()
        models = [m.get("id", "?") for m in response.json().get("data", [])]
        ok(f"VLM joignable — modèles servis : {models or ['(aucun)']}")
        if settings.vlm_model_name and settings.vlm_model_name not in models:
            fail(f"le modèle configuré '{settings.vlm_model_name}' n'est pas servi",
                 f"Choisir un des modèles listés ci-dessus, ou vérifier le backend ({base}).")
    except Exception as exc:
        fail(f"VLM injoignable ({base}) : {type(exc).__name__}",
             "Vérifier VLM_URL, le VPN/réseau et le certificat (VLM_CA_PEM).")

    # 6. Embedding (optionnel)
    if settings.embedding_url is None:
        warn("EMBEDDING_URL non renseignée",
             "Les étapes metadata-generation et hyq-embedding échoueront ; renseigner EMBEDDING_URL pour les activer.")
    else:
        ok(f"EMBEDDING_URL : {settings.embedding_url}")

    # 7. data/
    if settings.input_files_root.is_dir():
        pdf_count = sum(1 for _ in settings.input_files_root.rglob("*.pdf"))
        ok(f"input_files : {settings.input_files_root} ({pdf_count} PDF)")
    else:
        fail(f"dossier d'entrée absent : {settings.input_files_root}",
             "mkdir -p data/input_files puis y déposer les PDF (ou surcharger DATA_ROOT).")
    try:
        settings.output_files_root.mkdir(parents=True, exist_ok=True)
        probe = settings.output_files_root / ".doctor-write-check"
        probe.write_text("ok")
        probe.unlink()
        ok(f"sorties inscriptibles : {settings.output_files_root}")
    except OSError as exc:
        fail(f"sorties non inscriptibles ({settings.output_files_root}) : {exc}",
             "Vérifier les droits d'écriture ou surcharger DATA_ROOT.")

    typer.echo("Tout est prêt." if failures == 0 else f"{failures} problème(s) à corriger.")
    raise typer.Exit(0 if failures == 0 else 1)


@app.command()
def version() -> None:
    """Affiche la version installée."""
    typer.echo(importlib.metadata.version("afac-preprocessing"))


if __name__ == "__main__":
    app()
