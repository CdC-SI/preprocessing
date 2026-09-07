"""
markdown_tables_to_jsonl.py — Exporte les tables Markdown (natives, pipe |col|col|)
d'un document corrigé (_final.md) en JSONL.

Depuis l'intégration au pipeline (step 12, ``table-jsonl-normalize``), la
logique de parsing/conversion vit dans
``afac_preprocessing.steps.table_jsonl_normalize`` — ce script en est
maintenant le point d'entrée CLI (usage manuel/ad-hoc, hors pipeline), il
importe ces fonctions plutôt que de les dupliquer.

Deux sorties possibles, à partir du même parsing :
  1. Traçabilité (toujours) : un fichier .jsonl par table détectée, dans un dossier séparé
     (par défaut : tables_markdown/ à côté du markdown source) — n'affecte jamais le
     markdown utilisé pour l'embedding.
  2. Embedding (--embed-output) : réécrit le document entier en remplaçant chaque table
     Markdown par ses lignes JSONL, pour un usage où l'on veut que l'embedding porte sur
     des tables structurées plutôt que sur du Markdown pipe — au prix du surcoût de tokens
     déjà mesuré (répétition des clés de colonne à chaque ligne, cf. comparaison baseline).

Contrairement à csv_to_jsonlines.py (qui convertit les CSV extraits par Docling
avant toute correction VLM), ce script lit les tables APRÈS correction VLM.

Gère l'artefact de frontière de page : quand la correction VLM (page par page) réémet
la ligne d'en-tête au début d'une nouvelle page sans nouvelle ligne de séparateur
(|---|---|), cette ligne est reconnue comme un doublon de l'en-tête et ignorée plutôt
que traitée comme une ligne de données.

Usage :
    uv run python tools/markdown_tables_to_jsonl.py --markdown data/output_files_preprocessing/MonDoc/MonDoc_final.md
    uv run python tools/markdown_tables_to_jsonl.py --dotenv .env.test --stage5 data/output_files_preprocessing
    uv run python tools/markdown_tables_to_jsonl.py --markdown ... --output-dir data/output_files_preprocessing/MonDoc/tables_markdown
    uv run python tools/markdown_tables_to_jsonl.py --markdown MonDoc_final.md --embed-output MonDoc_final_embed.md
"""
import argparse
import logging
import os
import sys
from pathlib import Path

from afac_preprocessing.settings import _find_project_root
from afac_preprocessing.steps.table_jsonl_normalize import (
    parse_markdown_tables,
    render_markdown_with_jsonl_tables,
    write_tables_jsonl,
)

_log = logging.getLogger(__name__)


def project_root() -> Path:
    """Racine du projet (dossier contenant pyproject.toml), PROJECT_ROOT prioritaire.

    Helper local depuis le lot 8 : la couche de compat ``utils/`` a été
    supprimée et ce script reste un outil hors pipeline (décision n°14) —
    il migre au lot 9.
    """
    if "PROJECT_ROOT" in os.environ:
        return Path(os.environ["PROJECT_ROOT"]).resolve()
    return _find_project_root()


def resolve_doc_name(args: argparse.Namespace, *, primary_flag: str = "--doc-name") -> str:
    """DOC_NAME depuis --doc-name, le .env de --dotenv, ou l'environnement.

    Helper local (voir project_root ci-dessus) — même message d'erreur qu'avant.
    """
    from dotenv import load_dotenv

    doc_name = (getattr(args, "doc_name", None) or "").strip()
    if doc_name:
        return doc_name
    dotenv = getattr(args, "dotenv", None)
    if dotenv:
        resolved = Path(dotenv).resolve()
        if not resolved.exists():
            raise SystemExit(f"Error: .env file not found — {resolved}")
        load_dotenv(dotenv_path=resolved)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if doc_name:
        return doc_name
    raise SystemExit(
        f"Error: provide {primary_flag} <value>, or --dotenv <file> with DOC_NAME, "
        "or set the DOC_NAME variable in the environment."
    )


# CLI
def parse_args() -> argparse.Namespace:
    # Résolu ici, pas au chargement du module : importer ce fichier (ex. pour
    # réutiliser resolve_doc_name dans un test) ne doit pas déclencher une
    # recherche de racine de projet sur le disque en effet de bord.
    default_stage5 = project_root() / "data" / "output_files_preprocessing"

    parser = argparse.ArgumentParser(
        description=(
            "Exporte les tables Markdown natives d'un document corrigé (_final.md) en JSONL, "
            "pour traçabilité — n'affecte jamais le markdown utilisé pour l'embedding."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python tools/markdown_tables_to_jsonl.py \\\n"
            "      --markdown data/output_files_preprocessing/MonDoc/MonDoc_final.md\n"
            "  uv run python tools/markdown_tables_to_jsonl.py --dotenv .env.test --stage5 data/output_files_preprocessing\n"
        ),
    )
    parser.add_argument(
        "--doc-name",
        type=str,
        default=None,
        help="Nom du document sans extension. Si absent, résout DOC_NAME depuis --dotenv ou l'environnement.",
    )
    parser.add_argument(
        "--stage5",
        type=Path,
        default=default_stage5,
        help=f"Racine de sortie du pipeline (contient <doc_name>/<doc_name>_final.md). Défaut : {default_stage5}.",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path,
        default=None,
        help="Chemin explicite vers le markdown à parser. Défaut : <stage5>/<doc_name>/<doc_name>_final.md.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Dossier de sortie des .jsonl. Défaut : <dossier du markdown>/tables_markdown/.",
    )
    parser.add_argument(
        "--embed-output",
        type=Path,
        default=None,
        help=(
            "Si fourni, écrit aussi le document entier avec les tables remplacées par du JSONL "
            "à ce chemin (ex. : <doc>_final_embed.md) — destiné à être utilisé comme source de "
            "l'embedding à la place du markdown natif."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def resolve_markdown(args: argparse.Namespace, doc_name: str) -> Path:
    if args.markdown:
        return args.markdown.resolve()
    return args.stage5 / doc_name / f"{doc_name}_final.md"


def resolve_output_dir(args: argparse.Namespace, markdown_path: Path) -> Path:
    if args.output_dir:
        return args.output_dir.resolve()
    return markdown_path.parent / "tables_markdown"


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    doc_name = args.doc_name or resolve_doc_name(args, primary_flag="--doc-name")
    markdown_path = resolve_markdown(args, doc_name)
    if not markdown_path.exists():
        raise SystemExit(f"Erreur : fichier markdown introuvable — {markdown_path}")

    output_dir = resolve_output_dir(args, markdown_path)

    text = markdown_path.read_text(encoding="utf-8")
    tables = parse_markdown_tables(text)

    if not tables:
        _log.warning("Aucune table Markdown détectée dans %s", markdown_path)
        if args.embed_output:
            args.embed_output.parent.mkdir(parents=True, exist_ok=True)
            args.embed_output.write_text(text, encoding="utf-8")
            _log.info("Aucune table à convertir — copie inchangée → %s", args.embed_output)
        sys.exit(0)

    _log.info("%d table(s) détectée(s) dans %s", len(tables), markdown_path.name)
    write_tables_jsonl(tables, output_dir, doc_name)
    _log.info("Terminé. Sortie : %s", output_dir)

    if args.embed_output:
        embed_text = render_markdown_with_jsonl_tables(text)
        args.embed_output.parent.mkdir(parents=True, exist_ok=True)
        args.embed_output.write_text(embed_text, encoding="utf-8")
        _log.info(
            "Document réécrit pour l'embedding (%d → %d caractères) → %s",
            len(text), len(embed_text), args.embed_output,
        )


if __name__ == "__main__":
    main()
