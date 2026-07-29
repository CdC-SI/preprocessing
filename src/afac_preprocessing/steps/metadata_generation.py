"""Étape metadata-generation — CONTENT + METADATA + EMBEDDING → CSV final.

Conversion de ``metadata/metadata_generation.py`` (vague D). Toutes les
fonctions de construction (get_*, build_metadata, write_csv_row…) sont
DÉPLACÉES telles quelles (invariant n°1). Les deux collaborateurs VLM
(``MetadataEnhancer``, ``DocumentEmbedder`` — piège P4) portent les appels
modèle, en async (contrainte C2), via les clients du ClientBundle.

Champs du bloc METADATA : voir build_metadata (inchangé).
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from .document_embedder import DocumentEmbedder
from .metadata_enhancer import MetadataEnhancer

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)

VISIBILITY_DEFAULT = "internal"
ISO_8601_FMT = "%Y-%m-%dT%H:%M:%SZ"


# Hiérarchie (folder_source) — déplacé tel quel
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
    chemin à plat ("MonDoc.pdf", un seul segment — DOC_PATH absent) ou chemin absolu
    (document hors de data/input_files/) — sinon parts[0] vaudrait respectivement le nom
    de fichier ou la racine du système de fichiers ("/").
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


# Input dir – JSON Docling — déplacé tel quel
def load_input_json(input_dir: Path, doc_name: str) -> dict:
    """
    :param input_dir: Dossier contenant le JSON Docling (sortie de l'étape 01)
    :param doc_name: Nom du document sans extension
    :return: JSON Docling du document
    """
    path = input_dir / doc_name / f"{doc_name}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_doctype(doc_json: dict) -> str:
    """
    Mimetype mapping pour déterminer le type de document.

    :param doc_json: JSON Docling du document
    :return: Type de document (pdf, docx, html, ...)
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
    :return: Nombre de pages du document
    """
    return len(doc_json.get("pages", {}))


def find_version_table(doc_json: dict):
    """
    Retourne (cells, headers) de la première table ayant une colonne 'Version'.

    :param doc_json: JSON Docling du document
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
    :return: Version du document ou chaîne vide si non trouvée
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
    :return: Date en ISO 8601 ou chaîne vide
    """
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime(ISO_8601_FMT)
        except ValueError:
            continue
    return ""


# Image dir – images extraites — déplacé tel quel
def get_images(image_dir: Path, doc_name: str) -> list[str]:
    """
    Retourne la liste des images extraites du document (used_images/).

    :param image_dir: Dossier contenant les images extraites (used_images/)
    :param doc_name: Nom du document sans extension
    :return: Liste des noms de fichiers image
    """
    img_dir = image_dir / doc_name / "used_images"
    if not img_dir.exists():
        return []
    return sorted(
        f.name for f in img_dir.iterdir()
        if f.suffix.lower() in (".png", ".jpg", ".jpeg")
    )


# URL dir – hyperlinks — déplacé tel quel
def read_jsonl(path: Path) -> list[dict]:
    """
    Lit un fichier JSONL et retourne une liste de dictionnaires.

    :param path: Chemin vers le fichier JSONL
    :return: Liste de dictionnaires
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def get_outgoing_links(url_dir: Path, doc_name: str) -> list[dict]:
    """
    Retourne la liste des liens hypertextes extraits du document.

    :param url_dir: Dossier contenant les hyperliens extraits
    :param doc_name: Nom du document sans extension
    :return: Liste de liens sortants
    """
    jsonl = url_dir / doc_name / f"hyperlinks_data_{doc_name}.jsonl"
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


def get_incoming_links(url_dir: Path, doc_name: str) -> list[dict]:
    """
    Parcourt les hyperlinks de tous les autres documents pour trouver des références à doc_name.

    :param url_dir: Dossier contenant les hyperliens extraits
    :param doc_name: Nom du document sans extension
    :return: Liste de liens entrants
    """
    if not url_dir.exists():
        return []
    needle = doc_name.lower()
    incoming = []
    for other_dir in sorted(url_dir.iterdir()):
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


# Markdown dir – markdown final — déplacé tel quel
def _resolve_markdown_file(markdown_dir: Path, doc_name: str) -> Path | None:
    """
    Résout le fichier markdown à utiliser comme CONTENT (donc comme source de l'embedding).

    Préfère <doc>_final_embed.md (tables remplacées par du JSONL, produit par
    markdown_tables_to_jsonl.py --embed-output) s'il existe, sinon <doc>_final.md.
    Rétrocompatible : les documents sans _final_embed.md (v1/v2/baseline) sont inchangés.
    """
    for suffix in ("_final_embed.md", "_final.md"):
        candidate = markdown_dir / doc_name / f"{doc_name}{suffix}"
        if candidate.exists():
            return candidate
    return None


def get_markdown_info(markdown_dir: Path, doc_name: str) -> tuple[str, int]:
    """
    Retourne (content_filename, chunk_count).

    :param markdown_dir: Dossier contenant le markdown final
    :param doc_name: Nom du document sans extension
    :return: Tuple (nom du fichier markdown, nombre de chunks)
    """
    resolved = _resolve_markdown_file(markdown_dir, doc_name) if markdown_dir.exists() else None
    if resolved:
        return resolved.name, 1
    return f"{doc_name}_final.md", 0


def get_markdown_content(markdown_dir: Path, doc_name: str) -> str:
    """
    Retourne le contenu markdown du document depuis markdown_dir — cf.
    _resolve_markdown_file pour la préférence _final_embed.md / _final.md.
    C'est ce texte qui devient la colonne CONTENT du CSV final ET la source de
    l'embedding.

    :param markdown_dir: Dossier contenant le markdown final
    :param doc_name: Nom du document sans extension
    :return: Contenu markdown
    """
    resolved = _resolve_markdown_file(markdown_dir, doc_name)
    return resolved.read_text(encoding="utf-8") if resolved else ""


def get_pdf_creation_date(pdf_path: Path) -> str:
    """
    Lit la date de création du PDF via fitz (PyMuPDF) et retourne en ISO 8601.

    :param pdf_path: Chemin vers le fichier PDF
    :return: Date de création en ISO 8601 ou chaîne vide
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


# Construction du bloc metadata — déplacé tel quel
def build_metadata(
    relative_doc_path: str,
    folder_source: Path,
    input_dir: Path,
    image_dir: Path,
    url_dir: Path,
    markdown_dir: Path,
    embedding_model_name: str,
) -> dict:
    """
    Construit le bloc de metadata pour un document donné.

    :param relative_doc_path: Chemin relatif dans folder_source (ex: "Taxation/MonDoc.pdf")
    :param folder_source: Racine de la hiérarchie documentaire
    :param input_dir: Dossier contenant le JSON Docling (sortie de l'étape 01)
    :param image_dir: Dossier contenant les images extraites (used_images/)
    :param url_dir: Dossier contenant les hyperliens extraits
    :param markdown_dir: Dossier contenant le markdown final
    :param embedding_model_name: Nom du modèle d'embedding (résolu depuis la config VLM)
    :return: Dictionnaire de metadata
    """
    doc_name = Path(relative_doc_path).stem
    doc_name_extension = Path(relative_doc_path).name
    hierarchy = get_hierarchy(folder_source, relative_doc_path)
    doc_json = load_input_json(input_dir, doc_name)
    content_file, chunk_count = get_markdown_info(markdown_dir, doc_name)
    created_at = get_pdf_creation_date(folder_source / relative_doc_path)
    updated_at = datetime.now(UTC).strftime(ISO_8601_FMT)
    page_count = get_page_count(doc_json)

    return {
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, str(relative_doc_path))),
        "user_uuid": "",
        "source": hierarchy["source"],
        "title": doc_name_extension,
        "doctype": get_doctype(doc_json),
        "version": get_version(doc_json),
        "visibility": VISIBILITY_DEFAULT,
        "language": "fr",
        "outgoing_links": get_outgoing_links(url_dir, doc_name),
        "incoming_links": get_incoming_links(url_dir, doc_name),
        "created_at": created_at,
        "updated_at": updated_at,
        "media_type": get_images(image_dir, doc_name),
        "parent_label": hierarchy["parent_label"],
        "children_label": hierarchy["children_label"],
        "sibling": hierarchy["sibling"],
        "content": content_file,
        "page_count": page_count,
        "page_num": get_page_num(page_count),
        "chunk_count": chunk_count,
        "embedding_model": embedding_model_name,
    }


# Écriture CSV — déplacé tel quel
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
    :param metadata: Dictionnaire de metadata
    :param content: Contenu markdown du document (markdown_dir)
    :param embedding: Vecteur d'embedding sous forme de chaîne CSV
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kept_rows = _rows_excluding_title(output_path, metadata.get("title", ""))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
        writer.writerows(kept_rows)
        writer.writerow([content, json.dumps(metadata, ensure_ascii=False), embedding])


class MetadataGenerationStep(PipelineStep):
    """Construit la ligne CONTENT | METADATA | EMBEDDING du document → _final.csv."""

    name = "metadata-generation"
    description = "Metadata + embedding → CSV final"
    requires_vlm = True

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.final_markdown, ctx.workspace.docling_json]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        ws = ctx.workspace
        return [ws.final_csv, ws.resume_markdown, ws.intent_json, ws.hyq_json,
                ws.embedding_json]

    def _relative_doc_path(self, ctx: PipelineContext) -> str:
        """Équivalent du DOC_PATH historique : chemin relatif à input_files/,
        ou chemin absolu si le document vit ailleurs, ou <doc>.pdf à plat."""
        ws = ctx.workspace
        if ws.relative_dir != Path():
            return str(ws.relative_dir / ws.source_pdf.name)
        try:
            return str(ws.source_pdf.resolve().relative_to(
                ctx.settings.input_files_root.resolve()
            ))
        except ValueError:
            if ws.source_pdf.is_absolute():
                return str(ws.source_pdf)
            return f"{ws.doc_name}.pdf"

    def execute(self, ctx: PipelineContext) -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # ⚠ PAS asyncio.run() (P7)

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        out_root = ctx.settings.output_files_root
        try:
            # VLM enrichment (resume, intent, hyq) — collaborateur MetadataEnhancer
            enhancer = MetadataEnhancer(ctx.vlm())
            enrichment = await enhancer.run(ws)

            # Embedding du contenu markdown — collaborateur DocumentEmbedder
            embedder = DocumentEmbedder(
                ctx.embeddings(), ctx.settings.embedding_model_name
            )
            embedding_str, embedding_model_name = await embedder.run(ws)

            # Construction des metadata structurées (mêmes racines que les
            # défauts historiques : tout vit sous output_files_preprocessing/)
            metadata = build_metadata(
                self._relative_doc_path(ctx),
                folder_source=ctx.settings.input_files_root,
                input_dir=out_root,
                image_dir=out_root,
                url_dir=out_root,
                markdown_dir=out_root,
                embedding_model_name=embedding_model_name,
            )
            metadata["resume"] = enrichment["resume"]
            metadata["intent"] = ", ".join(enrichment["intent"])
            metadata["hyq"] = ", ".join(enrichment["hyq"])

            content = get_markdown_content(out_root, ws.doc_name)
            write_csv_row(ws.final_csv, metadata, content, embedding_str)
        except Exception as exc:
            raise StepFailed(f"metadata-generation failed on {ws.doc_name}: {exc}") from exc

        _log.info("Ligne ajoutée dans : %s", ws.final_csv)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
