"""Agrégation des CSV par document en un CSV global par dossier racine (lot F2).

Une **racine** est un enfant direct de ``data/input_files/`` (``afac``, et tout
futur corpus). Le fichier global est ``<sortie>/<racine>/<racine>.csv``
(décision n°17), à côté de l'arborescence des documents :

    output_files_preprocessing/
    └── afac/
        ├── afac.csv                                   ← produit ici
        └── Adhésion/<doc>/metadata/<doc>_final.csv    ← inchangé

Ce n'est **pas** une 14ᵉ étape du pipeline : une étape s'exécute par document
et n'a aucune vision du batch. Elle réécrirait le CSV global N fois par batch,
et sa sortie dépendrait des *autres* documents — ce qui casserait le contrat
``inputs()``/``outputs()``. C'est une action de fin de batch, appelée par
``Pipeline.run_batch()`` et exposée en ``afac-preprocess aggregate``.
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Format du CSV du pipeline — source unique, partagée avec write_csv_row
# (steps/metadata_generation.py) : le CSV global doit être lisible exactement
# comme les CSV par document.
CSV_HEADER = ["CONTENT", "METADATA", "EMBEDDING"]
CSV_QUOTING = csv.QUOTE_ALL

# La colonne EMBEDDING fait plusieurs Ko par ligne — la limite par défaut de
# csv (131 072 caractères) est trop basse pour un corpus réel.
_FIELD_SIZE_LIMIT_SET = False


def _ensure_field_size_limit() -> None:
    global _FIELD_SIZE_LIMIT_SET
    if not _FIELD_SIZE_LIMIT_SET:
        csv.field_size_limit(sys.maxsize)
        _FIELD_SIZE_LIMIT_SET = True


def find_document_csvs(root_dir: Path) -> list[Path]:
    """Les CSV par document du sous-arbre, triés par chemin RELATIF.

    Le tri sur le chemin relatif — et non sur le nom de fichier — reproduit
    exactement l'ordre de traitement du batch, qui parcourt les PDF via
    ``sorted(rglob("*.pdf"))``. L'exigence « l'ordre des lignes suit l'ordre de
    traitement » est donc satisfaite par construction.
    """
    return sorted(
        root_dir.rglob("metadata/*_final.csv"),
        key=lambda p: p.relative_to(root_dir).as_posix(),
    )


def _data_rows(csv_path: Path) -> list[list[str]]:
    """Lignes de données d'un CSV par document, en-tête exclu.

    Les lignes sont reprises **telles quelles** : on concatène des lignes CSV,
    on ne régénère pas de metadata (pas de reparse du JSON METADATA).
    """
    _ensure_field_size_limit()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    return rows[1:] if rows[0] == CSV_HEADER else rows


def aggregate_root_csv(out_root: Path, root_name: str) -> Path:
    """Concatène les ``<doc>_final.csv`` du sous-arbre ``<out_root>/<root_name>/``
    dans ``<out_root>/<root_name>/<root_name>.csv``.

    Reconstruction complète, **jamais un append** : un append laisserait les
    lignes de documents supprimés et doublerait les lignes au rerun — le bug
    exact que ``_rows_excluding_title`` évite au niveau document. On rescanne,
    on réécrit. L'opération est donc idempotente.

    :param out_root: Racine des sorties (data/output_files_preprocessing/)
    :param root_name: Nom du dossier racine (ex. "afac")
    :return: Chemin du CSV global écrit
    """
    root_dir = out_root / root_name
    output_path = root_dir / f"{root_name}.csv"

    csv_paths = [p for p in find_document_csvs(root_dir) if p != output_path]
    root_dir.mkdir(parents=True, exist_ok=True)

    n_rows = 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        for csv_path in csv_paths:
            rows = _data_rows(csv_path)
            writer.writerows(rows)
            n_rows += len(rows)

    _log.info(
        "CSV global écrit : %s (%d document(s), %d ligne(s))",
        output_path, len(csv_paths), n_rows,
    )
    return output_path


def is_document_dir(directory: Path) -> bool:
    """Vrai si *directory* est le dossier d'UN document, pas un dossier de corpus.

    Un dossier document porte ``metadata/<son nom>_final.csv`` (ou, avant
    l'étape metadata, ``<son nom>.doctags``). Le distinguer est indispensable :
    une sortie produite avant le lot F1 est plate, ses dossiers documents sont
    des enfants directs de la racine, et les prendre pour des corpus créerait
    un CSV « global » parasite dans chacun d'eux.
    """
    name = directory.name
    return (
        (directory / "metadata" / f"{name}_final.csv").exists()
        or (directory / f"{name}.doctags").exists()
    )


def discover_roots(out_root: Path) -> list[str]:
    """Dossiers racines présents dans la sortie.

    Une racine est un enfant direct de la sortie qui contient des documents
    sans en être un lui-même (cf. is_document_dir).
    """
    if not out_root.is_dir():
        return []
    return sorted(
        d.name for d in out_root.iterdir()
        if d.is_dir() and not is_document_dir(d) and any(d.rglob("metadata/*_final.csv"))
    )


def aggregate_all_roots(out_root: Path) -> list[Path]:
    """Agrège chaque racine trouvée — deux corpus produisent deux CSV
    indépendants, sans collision de nom."""
    return [aggregate_root_csv(out_root, name) for name in discover_roots(out_root)]
