"""
Stage 5 - Script de génération de metadata pour chaque document
Script 1 : metadata_generation_modular.py

Génère les metadata personnalisées pour chaque document (CONTENT + METADATA + EMBEDDING)
en lisant les sorties des stages 1-4 et en appelant les fonctions VLM d'enrichissement.

Usage :
    uv run python metadata_generation_modular.py --dotenv .env.test
    uv run python metadata_generation_modular.py --dotenv .env.test --doc-path "Taxation/DISPENSE/Annulation d'une dispense.pdf"
    uv run python metadata_generation_modular.py --dotenv .env.test --output ./out/docs.csv
"""
import argparse
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from enhancement_metadata_modular import run_enhancement
from embedding_metadata_modular import run_embedding
from utils.paths import project_root, resolve_doc_name

# Root
FOLDER_SOURCE  = Path(__file__).resolve().parent / "folder_source"
DEFAULT_OUTPUT_FILES = project_root() / "data" / "output_files"
DEFAULT_STAGE1 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE2 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE3 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE4 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE5 = DEFAULT_OUTPUT_FILES

VISIBILITY_DEFAULT = "internal"
ISO_8601_FMT = "%Y-%m-%dT%H:%M:%SZ"


# Hiérarchie (folder_source)

def get_hierarchy(folder_source: Path, relative_doc_path: str) -> dict:
    """
    Déduit source, parent_label, children_label et sibling
    depuis le chemin relatif dans folder_source.

    Exemple : "Taxation/DISPENSE/Annulation d'une dispense.pdf"
        source -> "afac"
        parent_label -> ["DISPENSE"]
        children_label -> sous-dossiers présents dans DISPENSE
        sibling -> autres fichiers présents dans DISPENSE
    """
    doc_path = Path(relative_doc_path)
    source = "afac"
    parts = doc_path.parts

    parent_dir = (folder_source / doc_path).parent

    parent_label = [parts[-2]] if len(parts) >= 2 else []

    children_label = []
    if parent_dir.exists():
        children_label = sorted(
            directory.name for directory in parent_dir.iterdir()
            if directory.is_dir()
        )

    siblings = []
    if parent_dir.exists():
        siblings = sorted(
            fichier.name for fichier in parent_dir.iterdir()
            if fichier.is_file() and fichier.name != doc_path.name
        )

    return {
        "source": source,
        "parent_label": parent_label,
        "children_label": children_label,
        "sibling": siblings,
    }


# Stage 1 – JSON Docling
def load_stage1_json(stage1_dir: Path, doc_name: str) -> dict:
    """
    :param stage1_dir: Dossier stage1
    :type stage1_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: JSON Docling du document
    :rtype: dict
    """
    path = stage1_dir / doc_name / f"{doc_name}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_doctype(doc_json: dict) -> str:
    """
    Mimetype mapping pour déterminer le type de document.

    :param doc_json: JSON Docling du document
    :type doc_json: dict
    :return: Type de document (pdf, docx, html, ...)
    :rtype: str
    """
    mimetype = doc_json.get("origin", {}).get("mimetype", "")
    mapping = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/msword": "doc",
        "text/html": "html",
        "text/plain": "txt",
        "image/png": "png",
        "image/jpeg": "jpg",
    }
    return mapping.get(mimetype, mimetype.split("/")[-1] if "/" in mimetype else "unknown")


def get_page_count(doc_json: dict) -> int:
    """
    :param doc_json: JSON Docling du document
    :type doc_json: dict
    :return: Nombre de pages du document
    :rtype: int
    """
    return len(doc_json.get("pages", {}))


def find_version_table(doc_json: dict):
    """
    Retourne (cells, headers) de la première table ayant une colonne 'Version'.

    :param doc_json: JSON Docling du document
    :type doc_json: dict
    """
    for table in doc_json.get("tables", []):
        cells = table.get("data", {}).get("table_cells", [])
        headers = [c["text"].strip().lower() for c in cells if c.get("column_header")]
        if "version" in headers:
            return cells, headers
    return None, None


def get_version(doc_json: dict) -> str:
    """
    :param doc_json: JSON Docling du document
    :type doc_json: dict
    :return: Version du document ou chaîne vide si non trouvée
    :rtype: str
    """
    cells, _ = find_version_table(doc_json)
    if not cells:
        return ""
    version_col = next(
        (c["start_col_offset_idx"] for c in cells
         if c.get("column_header") and c["text"].strip().lower() == "version"),
        None,
    )
    if version_col is None:
        return ""
    versions = [
        c["text"].strip() for c in cells
        if not c.get("column_header")
        and c["start_col_offset_idx"] == version_col
        and c["text"].strip()
    ]
    return versions[-1] if versions else ""


def parse_date(raw: str) -> str:
    """
    Tente de parser une date à partir de formats courants et retourne en ISO 8601.

    :param raw: Date brute
    :type raw: str
    :return: Date en ISO 8601 ou chaîne vide
    :rtype: str
    """
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime(ISO_8601_FMT)
        except ValueError:
            continue
    return ""


# Stage 2 – images extraites
def get_images(stage2_dir: Path, doc_name: str) -> list[str]:
    """
    Retourne la liste des images extraites du document (used_images/).

    :param stage2_dir: Dossier stage2
    :type stage2_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Liste des noms de fichiers image
    :rtype: list[str]
    """
    img_dir = stage2_dir / doc_name / "used_images"
    if not img_dir.exists():
        return []
    return sorted(
        f.name for f in img_dir.iterdir()
        if f.suffix.lower() in (".png", ".jpg", ".jpeg")
    )


# Stage 3 – hyperlinks
def read_jsonl(path: Path) -> list[dict]:
    """
    Lit un fichier JSONL et retourne une liste de dictionnaires.

    :param path: Chemin vers le fichier JSONL
    :type path: Path
    :return: Liste de dictionnaires
    :rtype: list[dict]
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def get_outgoing_links(stage3_dir: Path, doc_name: str) -> list[dict]:
    """
    Retourne la liste des liens hypertextes extraits du document.

    :param stage3_dir: Dossier stage3
    :type stage3_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Liste de liens sortants
    :rtype: list[dict]
    """
    jsonl = stage3_dir / doc_name / f"hyperlinks_data_{doc_name}.jsonl"
    if not jsonl.exists():
        return []
    return [
        {
            "text": outgoing.get("text", ""),
            "url": outgoing.get("hyperlink", ""),
            "page": outgoing.get("page_number", 0),
        }
        for outgoing in read_jsonl(jsonl)
    ]


def get_incoming_links(stage3_dir: Path, doc_name: str) -> list[dict]:
    """
    Parcourt les hyperlinks de tous les autres documents pour trouver des références à doc_name.

    :param stage3_dir: Dossier stage3
    :type stage3_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Liste de liens entrants
    :rtype: list[dict]
    """
    if not stage3_dir.exists():
        return []
    needle = doc_name.lower()
    incoming = []
    for other_dir in sorted(stage3_dir.iterdir()):
        if not other_dir.is_dir() or other_dir.name == doc_name:
            continue
        jsonl = other_dir / f"hyperlinks_data_{other_dir.name}.jsonl"
        if not jsonl.exists():
            continue
        for obj in read_jsonl(jsonl):
            if needle in obj.get("text", "").lower() or needle in obj.get("hyperlink", "").lower():
                incoming.append({
                    "from_doc": other_dir.name,
                    "text": obj.get("text", ""),
                    "url": obj.get("hyperlink", ""),
                })
    return incoming


# Stage 4 – markdown final
def get_stage4_info(stage4_dir: Path, doc_name: str) -> tuple[str, int]:
    """
    Retourne (content_filename, chunk_count).

    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Tuple (nom du fichier markdown, nombre de chunks)
    :rtype: tuple[str, int]
    """
    if not stage4_dir.exists():
        return f"{doc_name}_vlm_check.md", 0

    single = stage4_dir / doc_name / f"{doc_name}_vlm_check.md"
    if single.exists():
        return single.name, 1

    return f"{doc_name}_vlm_check.md", 0


def get_stage4_content(stage4_dir: Path, doc_name: str) -> str:
    """
    Retourne le contenu markdown du document depuis stage4.

    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Contenu markdown
    :rtype: str
    """
    single = stage4_dir / doc_name / f"{doc_name}_vlm_check.md"
    if single.exists():
        return single.read_text(encoding="utf-8")
    return ""


def get_pdf_creation_date(pdf_path: Path) -> str:
    """
    Lit la date de création du PDF via fitz (PyMuPDF) et retourne en ISO 8601.

    :param pdf_path: Chemin vers le fichier PDF
    :type pdf_path: Path
    :return: Date de création en ISO 8601 ou chaîne vide
    :rtype: str
    """
    try:
        with fitz.open(str(pdf_path)) as doc:
            raw = doc.metadata.get("creationDate", "")
        if not raw:
            return ""
        raw = raw.lstrip("D:").replace("'", "").split("+")[0].split("-")[0].rstrip("Z")
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").strftime(ISO_8601_FMT)
    except Exception:
        return ""


def get_page_num(page_count: int) -> str:
    """Retourne les numéros de page sous forme de chaîne CSV. Ex: 3 pages -> '1,2,3'."""
    if page_count <= 0:
        return ""
    return ",".join(str(i) for i in range(1, page_count + 1))


# Construction du bloc metadata
def build_metadata(
    relative_doc_path: str,
    folder_source: Path,
    stage1_dir: Path,
    stage2_dir: Path,
    stage3_dir: Path,
    stage4_dir: Path,
    embedding_model_name: str,
) -> dict:
    """
    Construit le bloc de metadata pour un document donné.

    :param relative_doc_path: Chemin relatif dans folder_source (ex: "Taxation/MonDoc.pdf")
    :type relative_doc_path: str
    :param folder_source: Racine de la hiérarchie documentaire
    :type folder_source: Path
    :param stage1_dir: Dossier stage1
    :type stage1_dir: Path
    :param stage2_dir: Dossier stage2
    :type stage2_dir: Path
    :param stage3_dir: Dossier stage3
    :type stage3_dir: Path
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param embedding_model_name: Nom du modèle d'embedding (résolu depuis la config VLM)
    :type embedding_model_name: str
    :return: Dictionnaire de metadata
    :rtype: dict
    """
    doc_name = Path(relative_doc_path).stem
    doc_name_extension = Path(relative_doc_path).name
    hierarchy = get_hierarchy(folder_source, relative_doc_path)
    doc_json = load_stage1_json(stage1_dir, doc_name)
    content_file, chunk_count = get_stage4_info(stage4_dir, doc_name)
    created_at = get_pdf_creation_date(folder_source / relative_doc_path)
    updated_at = datetime.now(timezone.utc).strftime(ISO_8601_FMT)
    page_count = get_page_count(doc_json)

    return {
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, str(relative_doc_path))), # "uuid": str(uuid.uuid4()),  # random
        "user_uuid": "",
        "source": hierarchy["source"],
        "title": doc_name_extension,
        "doctype": get_doctype(doc_json),
        "version": get_version(doc_json),
        "visibility": VISIBILITY_DEFAULT,
        "language": "fr",
        "outgoing_links": get_outgoing_links(stage3_dir, doc_name),
        "incoming_links": get_incoming_links(stage3_dir, doc_name),
        "created_at": created_at,
        "updated_at": updated_at,
        "media_type": get_images(stage2_dir, doc_name),
        "parent_label": hierarchy["parent_label"],
        "children_label": hierarchy["children_label"],
        "sibling": hierarchy["sibling"],
        "content": content_file,
        "page_count": page_count,
        "page_num": get_page_num(page_count),
        "chunk_count": chunk_count,
        "embedding_model": embedding_model_name,
    }


# Écriture CSV
def _rows_excluding_title(output_path: Path, doc_title: str) -> list[list[str]]:
    """Lit le CSV existant et retourne toutes les lignes sauf celle du document identifié par doc_title."""
    if not output_path.exists():
        return []
    with open(output_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header = rows[0]
    meta_idx = header.index("METADATA") if "METADATA" in header else 1
    kept: list[list[str]] = []
    for row in rows[1:]:
        try:
            if json.loads(row[meta_idx]).get("title") == doc_title:
                continue
        except (json.JSONDecodeError, IndexError):
            pass
        kept.append(row)
    return kept


def write_csv_row(output_path: Path, metadata: dict, content: str = "", embedding: str = "") -> None:
    """
    Écrit (ou remplace) la ligne du document dans le CSV final (CONTENT | METADATA | EMBEDDING).
    Idempotent : si une ligne avec le même titre existe déjà, elle est remplacée.

    :param output_path: Chemin vers le fichier CSV de sortie
    :type output_path: Path
    :param metadata: Dictionnaire de metadata
    :type metadata: dict
    :param content: Contenu markdown du document (stage4)
    :type content: str
    :param embedding: Vecteur d'embedding sous forme de chaîne CSV
    :type embedding: str
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kept_rows = _rows_excluding_title(output_path, metadata.get("title", ""))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
        writer.writerows(kept_rows)
        writer.writerow([content, json.dumps(metadata, ensure_ascii=False), embedding])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère les metadata d'un document (CONTENT + METADATA + EMBEDDING) et les écrit dans le CSV final.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python metadata_generation_modular.py --dotenv .env.test\n"
            "  uv run python metadata_generation_modular.py --dotenv .env.test "
            "--doc-path \"Taxation/DISPENSE/Annulation d'une dispense.pdf\"\n"
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger (DOC_NAME, VLM_URL, EMBEDDING_URL, VLM_CA_PEM, ...).",
    )
    parser.add_argument(
        "--doc-path",
        type=str,
        default=None,
        help=(
            "Chemin relatif du document dans folder_source pour la hiérarchie "
            "(ex: \"Taxation/DISPENSE/MonDoc.pdf\"). "
            "Si absent, construit <DOC_NAME>.pdf (structure plate, sans sous-dossier)."
        ),
    )
    parser.add_argument(
        "--folder-source", type=Path, default=FOLDER_SOURCE,
        help="Racine de la hiérarchie documentaire (défaut: metadata/folder_source/).",
    )
    parser.add_argument("--stage1", type=Path, default=DEFAULT_STAGE1)
    parser.add_argument("--stage2", type=Path, default=DEFAULT_STAGE2)
    parser.add_argument("--stage3", type=Path, default=DEFAULT_STAGE3)
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Fichier CSV de sortie (défaut: data/output_files/metadata/<DOC_NAME>_final.csv).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    doc_name = resolve_doc_name(args, primary_flag="--dotenv")
    dotenv_path = args.dotenv

    # --doc-path définit la position dans la hiérarchie folder_source.
    # Si absent, on utilise DOC_NAME.pdf (structure plate, pas de sous-dossier).
    relative_doc_path = args.doc_path or f"{doc_name}.pdf"
    output_path = args.output or (args.stage5 / doc_name / "metadata" / f"{doc_name}_final.csv")

    # Stage 5 – VLM enrichment (resume, intent, hyq)
    enrichment = run_enhancement(doc_name, args.stage4, args.stage5, dotenv_path=dotenv_path)

    # Stage 5 – embedding du contenu markdown (retourne aussi le nom du modèle)
    embedding_str, embedding_model_name = run_embedding(
        doc_name, args.stage4, args.stage5, dotenv_path=dotenv_path
    )

    # Construction des metadata structurées
    metadata = build_metadata(
        relative_doc_path,
        folder_source=args.folder_source,
        stage1_dir=args.stage1,
        stage2_dir=args.stage2,
        stage3_dir=args.stage3,
        stage4_dir=args.stage4,
        embedding_model_name=embedding_model_name,
    )
    metadata["resume"] = enrichment["resume"]
    metadata["intent"] = ", ".join(enrichment["intent"])
    metadata["hyq"] = ", ".join(enrichment["hyq"])

    content = get_stage4_content(args.stage4, doc_name)
    write_csv_row(output_path, metadata, content, embedding_str)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"\n→ Ligne ajoutée dans : {output_path}")


if __name__ == "__main__":
    main()
