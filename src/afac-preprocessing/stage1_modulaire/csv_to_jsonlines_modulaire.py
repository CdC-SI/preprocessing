"""
csv_to_jsonlines_modulaire.py — Conversion des tables CSV (Docling) en fichiers JSONL.

Pour chaque CSV trouvé dans le dossier d'entrée, produit un fichier .jsonl
contenant une ligne JSON par ligne du tableau.

Usage :
    uv run python csv_to_jsonlines_modulaire.py --input-dir data/output_files/MonDoc/tables
    uv run python csv_to_jsonlines_modulaire.py --dotenv .env.test
"""
import argparse
import logging
import os
import sys
from pathlib import Path

import jsonlines
import pandas as pd
from dotenv import load_dotenv

_log = logging.getLogger(__name__)


# Utilitaires CSV → JSONL
def deduplicate_columns(columns: list[str]) -> list[str]:
    """
    Docstring for deduplicate_columns
    Ajoute un suffixe numérique aux colonnes dupliquées (col, col_2, col_3…).

    :param columns: Description
    :type columns: list[str]
    :return: Description
    :rtype: list[str]
    """
    counts: dict[str, int] = {}
    result = []
    for col in columns:
        if col not in counts:
            counts[col] = 1
            result.append(col)
        else:
            counts[col] += 1
            result.append(f"{col}_{counts[col]}")
    return result


def safe_row_dict(row: pd.Series) -> dict:
    """
    Docstring for safe_row_dict
    Convertit une ligne en dict JSON-safe : NaN → None, toutes valeurs → str.

    :param row: Description
    :type row: pd.Series
    :return: Description
    :rtype: dict
    """
    return {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}


def _detect_header_row(csv_path: Path) -> int:
    """Détermine si le vrai header est en ligne 0 ou en ligne 1.

    Docling peut produire des colonnes numériques (0, 1, 2…) quand le header
    réel se trouve dans la première ligne de données. On lit nrows=1 une seule
    fois pour couvrir les deux vérifications (évite la triple lecture originale).

    Retourne 0 (header en row 0) ou 1 (header en row 1).
    """
    df_peek = pd.read_csv(csv_path, nrows=1)
    if df_peek.empty:
        return 0

    all_numeric_cols = all(str(col).strip().isdigit() for col in df_peek.columns)
    if not all_numeric_cols:
        return 0

    first_row = df_peek.iloc[0]
    values = [v for v in first_row if not pd.isna(v)] # on ignore les NaN, qui peuvent fausser la détection
    first_row_is_text = bool(values) and all(
        isinstance(v, str) and not str(v).replace(".", "").replace("-", "").isdigit()
        for v in values
    )
    return 1 if first_row_is_text else 0


def process_csv(csv_path: Path, output_dir: Path) -> bool:
    """
    Docstring for process_csv
    Convertit un CSV en JSONL dans output_dir. Retourne True si un fichier a été écrit.

    :param csv_path: Description
    :type csv_path: Path
    :param output_dir: Description
    :type output_dir: Path
    :return: Description
    :rtype: bool
    """
    header_row = _detect_header_row(csv_path)
    df = pd.read_csv(csv_path, header=header_row)
    df.columns = deduplicate_columns([str(col) for col in df.columns])

    if df.empty:
        _log.warning("%s — ignoré (tableau vide)", csv_path.name)
        return False

    jsonl_path = output_dir / csv_path.with_suffix(".jsonl").name
    with jsonlines.open(jsonl_path, mode="w") as writer:
        for _, row in df.iterrows():
            writer.write(safe_row_dict(row))

    _log.info("%s → %s", csv_path.name, jsonl_path.name)
    return True


# CLI
def parse_args() -> argparse.Namespace:
    """
    Docstring for parse_args
    
    :return: Description
    :rtype: Namespace
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convertit les tables CSV extraites par Docling en fichiers JSONL. "
            "Un .jsonl par CSV, une ligne JSON par ligne du tableau."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python csv_to_jsonlines_modulaire.py "
            "--input-dir data/output_files/MonDoc/tables\n"
            "  uv run python csv_to_jsonlines_modulaire.py "
            "--input-dir data/output_files/MonDoc/tables "
            "--output-dir data/output_files/MonDoc/jsonlines\n"
            "  uv run python csv_to_jsonlines_modulaire.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=Path,
        default=None,
        help=(
            "Dossier contenant les fichiers CSV à convertir. "
            "Si absent, résout data/output_files/<DOC_NAME>/tables/ depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help=(
            "Dossier de sortie pour les fichiers JSONL. "
            "Défaut : même dossier que --input-dir."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --input-dir est fourni.",
    )
    return parser.parse_args()


# Résolution des chemins
def _project_root() -> Path:
    """
    Docstring for _project_root
    
    :return: Description
    :rtype: Path
    """
    return Path(__file__).resolve().parent.parent


def resolve_input_dir(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_input_dir
    
    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.input_dir:
        return args.input_dir.resolve()
    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --input-dir <dossier>, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return _project_root() / "data" / "output_files" / doc_name / "tables"


def resolve_output_dir(args: argparse.Namespace, input_dir: Path) -> Path:
    """
    Docstring for resolve_output_dir
    
    :param args: Description
    :type args: argparse.Namespace
    :param input_dir: Description
    :type input_dir: Path
    :return: Description
    :rtype: Path
    """
    if args.output_dir:
        return args.output_dir.resolve()
    return input_dir  # par défaut : JSONL à côté des CSV


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_dir = resolve_input_dir(args)
    if not input_dir.exists():
        raise SystemExit(f"Erreur : dossier introuvable — {input_dir}")

    output_dir = resolve_output_dir(args, input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"Aucun fichier CSV trouvé dans : {input_dir}")

    _log.info("%d fichier(s) CSV trouvé(s) dans : %s", len(csv_files), input_dir)
    _log.info("Sortie JSONL dans : %s", output_dir)

    n_ok = n_skip = n_err = 0
    for csv_path in csv_files:
        try:
            if process_csv(csv_path, output_dir):
                n_ok += 1
            else:
                n_skip += 1
        except Exception as e:
            _log.exception("%s → erreur : %s", csv_path.name, e)
            n_err += 1

    _log.info(
        "Terminé — %d converti(s), %d ignoré(s) (vide), %d erreur(s).",
        n_ok, n_skip, n_err,
    )
    sys.exit(1 if n_err > 0 else 0)


if __name__ == "__main__":
    main()
