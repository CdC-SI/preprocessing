"""
metadata-generation stage, CONTENT + METADATA + EMBEDDING -> final CSV.

Conversion of metadata/metadata_generation.py. 
All construction functions (get_*, build_metadata, write_csv_row…) are MOVED as-is. 
The two VLM collaborators (MetadataEnhancer, DocumentEmbedder) handle the model calls,
asynchronously, via the ClientBundle clients.

METADATA block fields: the 21 structural fields are listed in the docstring of
build_metadata; resume / intent / hyq are then added by
MetadataGenerationStep._execute_async from the MetadataEnhancer (24 fields
in total).

title contains the filename WITHOUT extension and doctype
contains the extension only, in lowercase, the key name does not change, only
its source changes (the path extension instead of the Docling mimetype).
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from ..aggregate import CSV_HEADER, CSV_QUOTING
from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from .document_embedder import DocumentEmbedder
from .metadata_enhancer import MetadataEnhancer

if TYPE_CHECKING:
    from ..context import PipelineContext
    from ..workspace import DocumentWorkspace

_log = logging.getLogger(__name__)

VISIBILITY_DEFAULT = "internal"
ISO_8601_FMT = "%Y-%m-%dT%H:%M:%SZ"


# Hiérarchie (folder_source),moved as-is
def get_hierarchy(folder_source: Path, relative_doc_path: str) -> dict:
    """
    Infers source, parent_label, children_label, and sibling from the document's
    relative path inside folder_source (data/input_files/ by default).

    Example: "afac/Taxation/DISPENSE/Annulation d'une dispense.pdf"
    source -> "afac" (first path segment, root corpus folder,
    may vary: afac, or another source)
    parent_label -> ["DISPENSE"]
    children_label -> subfolders present in DISPENSE
    sibling -> other files present in DISPENSE

    Falls back to "afac" when the path does not contain a readable real
    <source>/... hierarchy:
    flat path ("MonDoc.pdf", single segment, DOC_PATH absent) or absolute path
    (document outside data/input_files/), otherwise parts[0] would respectively
    be the filename or the filesystem root ("/").
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


# Input dir, JSON Docling moved as-is
def load_input_json(workspace: DocumentWorkspace) -> dict:
    """
    :param workspace: Document workspace (contains all paths)
    :return: Document Docling JSON (empty if it does not exist)
    """
    path = workspace.docling_json
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_page_count(doc_json: dict) -> int:
    """
    :param doc_json: Docling JSON of the document
    :return: Number of pages in the document
    """
    return len(doc_json.get("pages", {}))


def find_version_table(doc_json: dict):
    """
    Returns (cells, headers) of the first table having a 'Version' column.

    :param doc_json: Docling JSON of the document
    """
    for table in doc_json.get("tables", []):
        cells = table.get("data", {}).get("table_cells", [])
        headers = [c["text"].strip().lower() for c in cells if c.get("column_header")]
        if "version" in headers:
            return cells, headers
    return None, None


def get_version(doc_json: dict) -> str:
    """
    :param doc_json: Docling JSON of the document
    :return: Document version or empty string if not found
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
    Attempts to parse a date from common formats and returns it in ISO 8601 format.

    raw: Raw date
    :return: Date in ISO 8601 format or empty string
    """
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime(ISO_8601_FMT)
        except ValueError:
            continue
    return ""


# Extracted images, path carried by the workspace
def get_images(workspace: DocumentWorkspace) -> list[str]:
    """
    Returns the list of images extracted from the document (used_images/).

    workspace: Document workspace (contains all paths)
    :return: List of image filenames
    """
    img_dir = workspace.used_images_dir
    if not img_dir.exists():
        return []
    return sorted(
        f.name for f in img_dir.iterdir()
        if f.suffix.lower() in (".png", ".jpg", ".jpeg")
    )


# URL dir, hyperlinks, moved as-is
def read_jsonl(path: Path) -> list[dict]:
    """
    Reads a JSONL file and returns a list of dictionaries.

    path: Path to the JSONL file
    :return: List of dictionaries
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def get_outgoing_links(workspace: DocumentWorkspace) -> list[dict]:
    """
    Returns the list of hyperlinks extracted from the document.

    workspace: Document workspace (contains all paths)
    :return: List of outgoing links
    """
    jsonl = workspace.hyperlinks_jsonl
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


def get_incoming_links(out_root: Path, doc_name: str) -> list[dict]:
    """
    Scans the hyperlinks of all other documents to find references to doc_name.

    The scan is RECURSIVE from the output root. 
    With a flat layout, iterdir() saw all documents, with the mirrored
    directory tree, it would only have seen those in the same subfolder, and
    incoming_links would have silently become incomplete. Sorting by document name
    (and not by path) preserves the historical list order.

    out_root: Output root directory (output_files_preprocessing/)
    doc_name: Document name without extension
    :return: List of incoming links
    """
    if not out_root.exists():
        return []
    needle = doc_name.lower()
    incoming = []
    jsonls = sorted(
        out_root.rglob("hyperlinks_data_*.jsonl"),
        key=lambda p: (p.parent.name, str(p)),
    )
    for jsonl in jsonls:
        other_name = jsonl.parent.name
        if other_name == doc_name or jsonl.name != f"hyperlinks_data_{other_name}.jsonl":
            continue
        for obj in read_jsonl(jsonl):
            if needle in obj.get("text", "").lower() or needle in obj.get("hyperlink", "").lower():
                incoming.append({
                    "from_doc": other_name,
                    "text": obj.get("text", ""),
                    "url": obj.get("hyperlink", ""),
                })
    return incoming


# Final markdown paths carried by the workspace
def _resolve_markdown_file(workspace: DocumentWorkspace) -> Path | None:
    """
    Resolves the markdown file to use as CONTENT (and therefore as the embedding
    source).

    Prefers <doc>_final_embed.md (tables replaced with JSONL, produced by
    markdown_tables_to_jsonl.py --embed-output) if it exists, otherwise
    <doc>_final.md.

    Backward compatible: documents without _final_embed.md (v1/v2/baseline) remain
    unchanged.
    """
    for candidate in (workspace.final_embed_markdown, workspace.final_markdown):
        if candidate.exists():
            return candidate
    return None


def get_markdown_info(workspace: DocumentWorkspace) -> tuple[str, int]:
    """
    Returns (content_filename, chunk_count).

    workspace: Document workspace (contains all paths)
    :return: Tuple (markdown filename, number of chunks)
    """
    resolved = _resolve_markdown_file(workspace)
    if resolved:
        return resolved.name, 1
    return workspace.final_markdown.name, 0


def get_markdown_content(workspace: DocumentWorkspace) -> str:
    """
    Returns the document's markdown content — see _resolve_markdown_file for
    the _final_embed.md / _final.md preference. This text becomes the CONTENT
    column of the final CSV AND the embedding source.

    workspace: Document workspace (contains all paths)
    :return: Markdown content
    """
    resolved = _resolve_markdown_file(workspace)
    return resolved.read_text(encoding="utf-8") if resolved else ""


def get_pdf_creation_date(pdf_path: Path) -> str:
    """
    Reads the PDF creation date via fitz (PyMuPDF) and returns it in ISO 8601 format.

    pdf_path: Path to the PDF file
    :return: Creation date in ISO 8601 format or empty string
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
    """Returns page numbers as a CSV string. Example: 3 pages -> '1,2,3'."""
    if page_count <= 0:
        return ""
    return ",".join(str(i) for i in range(1, page_count + 1))


# Metadata construction block
def build_metadata(
    relative_doc_path: str,
    folder_source: Path,
    workspace: DocumentWorkspace,
    out_root: Path,
    embedding_model_name: str,
) -> dict:
    """
    Builds the metadata block for a given document.

    Since batch F1, all document paths come from the workspace
    (which carries the mirrored directory tree); out_root is only used for
    the cross-document scan of incoming_links (decision no. 10).

    Produced fields (21):

    uuid: deterministic uuid5 based on the document relative path (stable between runs)
    user_uuid: empty string by default
    source: first path segment (e.g. "afac"), "afac" by default if the path is
    flat or absolute (see get_hierarchy)
    title: filename WITHOUT extension (e.g. "Annulation d'une dispense") — batch F3
    doctype: path extension only, lowercase (pdf, docx, html…) — batch F3,
    no longer the Docling mimetype
    version: last value of the "Version" column from the first Docling JSON table
    containing one, otherwise empty string
    visibility: "internal" by default (VISIBILITY_DEFAULT; "internal" / "public" / "sensitive")
    language: "fr" (all AF documents)
    outgoing_links: outgoing hyperlinks extracted from the document
    (hyperlinks_data_*.jsonl), as {text, url, page}
    incoming_links: references to the document found in other documents'
    hyperlinks, recursively scanned from out_root, as {from_doc, text, url}
    created_at: PDF creation date read via fitz (ISO 8601 YYYY-MM-DDTHH:MM),
    otherwise empty string
    updated_at: current UTC timestamp (ISO 8601)
    media_type: names of images extracted from the document (used_images/, .png/.jpg/.jpeg sorted)
    parent_label: document parent folder (e.g. ["DISPENSE"]), [] for flat paths
    children_label: subfolders present in the document folder
    sibling: other files present in the same folder (excluding the document itself)
    content: name of the markdown file used as CONTENT — <doc>_final_embed.md if
    it exists, otherwise <doc>_final.md (see _resolve_markdown_file)
    page_count: number of document pages (Docling JSON "pages" key)
    page_num: page numbers as a CSV string (e.g. "1,2,3"), empty string if page_count <= 0
    chunk_count: 1 if the final markdown exists, otherwise 0
    embedding_model: name of the embedding model (resolved by DocumentEmbedder
    from the VLM configuration)

    Three additional fields are added AFTER this call, in
    MetadataGenerationStep._execute_async, from the MetadataEnhancer (VLM):
    resume (document summary), intent and hyq (lists joined by ", ").

    The METADATA block written to the CSV therefore contains 24 fields.

    :param relative_doc_path: Chemin relatif dans folder_source (ex: "Taxation/MonDoc.pdf")
    :param folder_source: Racine de la hiérarchie documentaire (data/input_files/)
    :param workspace: Workspace du document (porte tous ses chemins de sortie)
    :param out_root: Racine des sorties, pour le balayage des incoming_links
    :param embedding_model_name: Nom du modèle d'embedding (résolu depuis la config VLM)
    :return: Dictionnaire de metadata
    """
    doc_name = Path(relative_doc_path).stem
    hierarchy = get_hierarchy(folder_source, relative_doc_path)
    doc_json = load_input_json(workspace)
    content_file, chunk_count = get_markdown_info(workspace)
    created_at = get_pdf_creation_date(folder_source / relative_doc_path)
    updated_at = datetime.now(UTC).strftime(ISO_8601_FMT)
    page_count = get_page_count(doc_json)

    return {
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, str(relative_doc_path))),
        "user_uuid": "",
        "source": hierarchy["source"],
        "title": doc_name,
        "doctype": Path(relative_doc_path).suffix.lstrip(".").lower(),
        "version": get_version(doc_json),
        "visibility": VISIBILITY_DEFAULT,
        "language": "fr",
        "outgoing_links": get_outgoing_links(workspace),
        "incoming_links": get_incoming_links(out_root, doc_name),
        "created_at": created_at,
        "updated_at": updated_at,
        "media_type": get_images(workspace),
        "parent_label": hierarchy["parent_label"],
        "children_label": hierarchy["children_label"],
        "sibling": hierarchy["sibling"],
        "content": content_file,
        "page_count": page_count,
        "page_num": get_page_num(page_count),
        "chunk_count": chunk_count,
        "embedding_model": embedding_model_name,
    }


# CSV writing, moved as-is
def _rows_excluding_title(output_path: Path, doc_title: str) -> list[list[str]]:
    """Reads the existing CSV and returns all rows except the one corresponding to the
    document identified by doc_title."""
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
    Writes (or replaces) the document row in the final CSV (CONTENT | METADATA | EMBEDDING).
    Idempotent: if a row with the same title already exists, it is replaced.

    output_path: Path to the output CSV file
    metadata: Metadata dictionary
    content: Document markdown content (markdown_dir)
    embedding: Embedding vector as a CSV string
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kept_rows = _rows_excluding_title(output_path, metadata.get("title", ""))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        # Header and quoting come from aggregate: the global CSV concatenates these
        # rows, the two formats must not be able to diverge.
        writer = csv.writer(f, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        writer.writerows(kept_rows)
        writer.writerow([content, json.dumps(metadata, ensure_ascii=False), embedding])


class MetadataGenerationStep(PipelineStep):
    """Builds the document CONTENT | METADATA | EMBEDDING row -> _final.csv."""

    name = "metadata-generation"
    description = "Metadata + embedding -> CSV final"
    requires_vlm = True

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.final_markdown, ctx.workspace.docling_json]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        ws = ctx.workspace
        return [ws.final_csv, ws.resume_markdown, ws.intent_json, ws.hyq_json,
                ws.embedding_json]

    def _relative_doc_path(self, ctx: PipelineContext) -> str:
        """Equivalent of the historical DOC_PATH: relative path to input_files/,
        or absolute path if the document is located elsewhere, or <doc>.pdf
        as a flat path."""
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
        return ctx.run_async(self._execute_async(ctx))  # Not asyncio.run()

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        out_root = ctx.settings.output_files_root
        try:
            # VLM enrichment (resume, intent, hyq), MetadataEnhancer collaborator
            enhancer = MetadataEnhancer(ctx.vlm())
            enrichment = await enhancer.run(ws)

            # Embedding of the markdown content, DocumentEmbedder collaborator
            embedder = DocumentEmbedder(
                ctx.embeddings(), ctx.settings.embedding_model_name
            )
            embedding_str, embedding_model_name = await embedder.run(ws)

            #Construction of structured metadata (same roots as the
            # historical defaults: everything lives under output_files_preprocessing/)
            metadata = build_metadata(
                self._relative_doc_path(ctx),
                folder_source=ctx.settings.input_files_root,
                workspace=ws,
                out_root=out_root,
                embedding_model_name=embedding_model_name,
            )
            metadata["resume"] = enrichment["resume"]
            metadata["intent"] = ", ".join(enrichment["intent"])
            metadata["hyq"] = ", ".join(enrichment["hyq"])

            content = get_markdown_content(ws)
            write_csv_row(ws.final_csv, metadata, content, embedding_str)
        except Exception as exc:
            raise StepFailed(f"metadata-generation failed on {ws.doc_name}: {exc}") from exc

        _log.info("Row added in : %s", ws.final_csv)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
