"""
Génération des metadata d'un document
Script 1 : metadata_generation.py

Construit la ligne finale (CONTENT + METADATA + EMBEDDING) d'un document en lisant les
sorties des stages 1-4, puis en appelant l'enrichissement VLM (resume / intent / hyq via
run_enhancement) et l'embedding du contenu markdown (via run_embedding). Ces metadata
servent ensuite au reranking et à l'embedding.

Écrit une ligne dans un CSV de 3 colonnes (idempotent : une ligne du même titre est
remplacée) :
    CONTENT   -> markdown final du document (stage4, _final_embed.md ou _final.md)
    METADATA  -> JSON généré par ce script (voir champs ci-dessous)
    EMBEDDING -> vecteur d'embedding du contenu (généré par run_embedding)

Champs du bloc METADATA :
- uuid : uuid5 déterministe basé sur le chemin relatif du document (stable entre les runs)
- user_uuid : chaîne vide par défaut
- source : premier segment du chemin (ex : "afac"), sinon "afac" par défaut
- title : nom du fichier avec extension (ex : "Annulation d'une dispense.pdf")
- doctype : pdf, docx, html, ... (déduit du mimetype Docling)
- version : version extraite d'une table du document (ex : "4.2"), sinon chaîne vide
- visibility : "internal" par défaut ("internal" / "public" / "sensitive")
- language : "fr" (tous les documents AF)
- outgoing_links : liens hypertextes sortants extraits du document (stage3)
- incoming_links : références au document trouvées dans les hyperlinks des autres documents
- created_at : date de création du PDF (ISO 8601 YYYY-MM-DDTHH:MM:SSZ), sinon chaîne vide
- updated_at : horodatage UTC courant (ISO 8601)
- media_type : liste des images extraites du document (stage2, used_images/)
- parent_label : dossier parent du document (ex : ["DISPENSE"])
- children_label : sous-dossiers présents dans le dossier du document
- sibling : autres fichiers présents dans le même dossier (hors document lui-même)
- content : nom du fichier markdown utilisé comme CONTENT (ex : "MonDoc_final.md")
- page_count : nombre de pages du document
- page_num : numéros de page en chaîne CSV (ex : "1,2,3")
- chunk_count : 1 si le markdown final existe, sinon 0
- embedding_model : nom du modèle d'embedding (résolu depuis la config VLM)
- resume : résumé du document (VLM, depuis le markdown stage4)
- intent : intentions du document, jointes en une chaîne (VLM)
- hyq : questions fréquentes, jointes en une chaîne (VLM)

Usage :
    uv run python metadata_generation.py --dotenv .env.test
    uv run python metadata_generation.py --dotenv .env.test --doc-path "afac/Taxation/DISPENSE/Annulation d'une dispense.pdf"
    uv run python metadata_generation.py --dotenv .env.test --output ./out/docs.csv
    uv run python metadata_generation.py --dotenv .env.test --skip-enhancement

Sortie par défaut :
    data/output_files_preprocessing/metadata/<DOC_NAME>_final.csv
"""

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from enhancement_metadata import run_enhancement
from embedding_metadata import run_embedding
from utils.paths import project_root, resolve_doc_name

# Root — data/input_files/ contient déjà la hiérarchie <source>/<thème>/[<sous-thème>/]<fichier>.pdf
# utilisée pour l'extraction (cf. resolve_input_pdf) ; on la réutilise directement pour la
# hiérarchie de métadonnées au lieu de maintenir un second dossier miroir séparé.
FOLDER_SOURCE  = project_root() / "data" / "input_files"
DEFAULT_OUTPUT_FILES = project_root() / "data" / "output_files_preprocessing"
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
    Déduit source, parent_label, children_label et sibling depuis le chemin relatif du
    document dans folder_source (data/input_files/ par défaut).

    Exemple : "afac/Taxation/DISPENSE/Annulation d'une dispense.pdf"
        source -> "afac"                 (premier segment du chemin — dossier racine du corpus,
                                           peut varier : afac, ou une autre source)
        parent_label -> ["DISPENSE"]
        children_label -> sous-dossiers présents dans DISPENSE
        sibling -> autres fichiers présents dans DISPENSE

    Retombe sur "afac" quand le chemin n'a pas de vraie hiérarchie <source>/... à lire :
    chemin à plat ("MonDoc.pdf", un seul segment — DOC_PATH absent, cf. main() cas 3) ou
    chemin absolu (DOC_PATH réglé sur un chemin absolu quand --input pointe hors de
    data/input_files/, cf. fullpipeline_modular_v3.py _resolve_doc_name()) — sinon parts[0]
    vaudrait respectivement le nom de fichier ou la racine du système de fichiers ("/").
    """
    doc_path = Path(relative_doc_path)
    parts = doc_path.parts
    source = parts[0] if len(parts) >= 2 and not doc_path.is_absolute() else "afac"

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
def _resolve_stage4_file(stage4_dir: Path, doc_name: str) -> Path | None:
    """
    Résout le fichier markdown à utiliser comme CONTENT (donc comme source de l'embedding —
    cf. get_stage4_content, appelée juste avant write_csv_row).

    Préfère <doc>_final_embed.md (tables remplacées par du JSONL, produit par
    markdown_tables_to_jsonl.py --embed-output) s'il existe, sinon <doc>_final.md.
    Rétrocompatible : les documents sans _final_embed.md (v1/v2/baseline) sont inchangés.
    """
    for suffix in ("_final_embed.md", "_final.md"):
        candidate = stage4_dir / doc_name / f"{doc_name}{suffix}"
        if candidate.exists():
            return candidate
    return None


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
    resolved = _resolve_stage4_file(stage4_dir, doc_name) if stage4_dir.exists() else None
    if resolved:
        return resolved.name, 1
    return f"{doc_name}_final.md", 0


def load_existing_enrichment(stage5_dir: Path, doc_name: str) -> dict:
    """
    Lit resume.md / intent.json / hyq.json déjà présents (au lieu d'appeler run_enhancement,
    qui régénère ces 3 champs via VLM depuis le markdown courant). Utilisé avec --skip-enhancement
    pour recalculer un embedding sans changer les questions HyQ déjà utilisées ailleurs.

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Dictionnaire {"resume": str, "intent": list[str], "hyq": list[str]}
    :rtype: dict
    """
    meta_dir = stage5_dir / doc_name / "metadata"
    resume_path = meta_dir / "resume.md"
    intent_path = meta_dir / "intent.json"
    hyq_path = meta_dir / "hyq.json"

    missing = [p.name for p in (resume_path, intent_path, hyq_path) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"--skip-enhancement requiert resume.md/intent.json/hyq.json existants dans {meta_dir} "
            f"— manquant(s) : {', '.join(missing)}"
        )

    return {
        "resume": resume_path.read_text(encoding="utf-8").strip(),
        "intent": json.loads(intent_path.read_text(encoding="utf-8")),
        "hyq": json.loads(hyq_path.read_text(encoding="utf-8")),
    }


def get_stage4_content(stage4_dir: Path, doc_name: str) -> str:
    """
    Retourne le contenu markdown du document depuis stage4 — cf. _resolve_stage4_file
    pour la préférence _final_embed.md / _final.md. C'est ce texte qui devient la
    colonne CONTENT du CSV final ET la source de l'embedding (run_embedding lit le
    même fichier via embedding_metadata._read_stage4).

    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Contenu markdown
    :rtype: str
    """
    resolved = _resolve_stage4_file(stage4_dir, doc_name)
    return resolved.read_text(encoding="utf-8") if resolved else ""


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
            "  uv run python metadata_generation.py --dotenv .env.test\n"
            "  uv run python metadata_generation.py --dotenv .env.test "
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
            "Chemin relatif du document dans folder_source (data/input_files/) pour la "
            "hiérarchie (ex: \"afac/Taxation/DISPENSE/MonDoc.pdf\"). "
            "Si absent, réutilise DOC_PATH (déjà résolu par --input dans pipeline_extraction.py/fullpipeline_modular_v3.py). "
            "Si DOC_PATH est aussi absent, construit <DOC_NAME>.pdf (structure plate, sans sous-dossier)."
        ),
    )
    parser.add_argument(
        "--folder-source", type=Path, default=FOLDER_SOURCE,
        help="Racine de la hiérarchie documentaire (défaut: data/input_files/).",
    )
    parser.add_argument("--stage1", type=Path, default=DEFAULT_STAGE1)
    parser.add_argument("--stage2", type=Path, default=DEFAULT_STAGE2)
    parser.add_argument("--stage3", type=Path, default=DEFAULT_STAGE3)
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Fichier CSV de sortie (défaut: data/output_files_preprocessing/metadata/<DOC_NAME>_final.csv).",
    )
    parser.add_argument(
        "--skip-enhancement",
        action="store_true",
        help=(
            "Ne pas régénérer resume/intent/hyq via VLM — lit resume.md, intent.json et hyq.json "
            "déjà présents dans <stage5>/<DOC_NAME>/metadata/. Utile pour recalculer l'embedding "
            "d'un document sans changer les questions HyQ déjà utilisées ailleurs (comparabilité)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        doc_name = resolve_doc_name(args, primary_flag="--dotenv")
        dotenv_path = args.dotenv

        # --doc-path définit la position dans la hiérarchie folder_source (data/input_files/).
        # Résolution : 1. --doc-path explicite  2. DOC_PATH (déjà résolu par --input dans
        # pipeline_extraction.py/fullpipeline_modular_v3.py, ou défini dans le .env chargé par resolve_doc_name
        # ci-dessus — propagé aux sous-processus par héritage d'environnement)  3. DOC_NAME.pdf
        # à plat (aucune hiérarchie disponible → parent/children/sibling resteront vides).
        relative_doc_path = args.doc_path or os.environ.get("DOC_PATH", "").strip() or f"{doc_name}.pdf"
        output_path = args.output or (args.stage5 / doc_name / "metadata" / f"{doc_name}_final.csv")

        # Stage 5 – VLM enrichment (resume, intent, hyq), ou lecture de l'existant si --skip-enhancement
        if args.skip_enhancement:
            enrichment = load_existing_enrichment(args.stage5, doc_name)
        else:
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

    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
