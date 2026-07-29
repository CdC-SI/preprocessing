"""Étape metadata-generation — CONTENT + METADATA + EMBEDDING → CSV final.

Conversion de ``metadata/metadata_generation.py`` (vague D). Toutes les
fonctions de construction (get_*, build_metadata, write_csv_row…) sont
DÉPLACÉES telles quelles (invariant n°1). Les deux collaborateurs VLM
(``MetadataEnhancer``, ``DocumentEmbedder`` — piège P4) portent les appels
modèle, en async (contrainte C2), via les clients du ClientBundle.

Champs du bloc METADATA : voir build_metadata.

⚠ Lot F3 : ``title`` porte le nom du fichier SANS extension et ``doctype``
l'extension seule, en minuscules — le nom de la clé ne change pas, seule sa
source change (l'extension du chemin au lieu du mimetype Docling). Effet de
bord assumé : ``_rows_excluding_title`` déduplique par ``title``, donc un CSV
écrit avant F3 ("MonDoc.pdf") ne matche plus une ligne post-F3 ("MonDoc") —
purger les ``*_final.csv`` avant le premier run post-F3 (décision n°13).
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
    from ..workspace import DocumentWorkspace

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
def load_input_json(workspace: DocumentWorkspace) -> dict:
    """
    :param workspace: Workspace du document (porte tous les chemins)
    :return: JSON Docling du document (vide s'il n'existe pas)
    """
    path = workspace.docling_json
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


# Images extraites — chemin porté par le workspace (lot F1)
def get_images(workspace: DocumentWorkspace) -> list[str]:
    """
    Retourne la liste des images extraites du document (used_images/).

    :param workspace: Workspace du document (porte tous les chemins)
    :return: Liste des noms de fichiers image
    """
    img_dir = workspace.used_images_dir
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


def get_outgoing_links(workspace: DocumentWorkspace) -> list[dict]:
    """
    Retourne la liste des liens hypertextes extraits du document.

    :param workspace: Workspace du document (porte tous les chemins)
    :return: Liste de liens sortants
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
    Parcourt les hyperlinks de tous les autres documents pour trouver des références à doc_name.

    ⚠ Lot F1, décision n°10 : le parcours est RÉCURSIF depuis la racine de
    sortie. En layout plat, ``iterdir()`` voyait tous les documents ; avec
    l'arborescence miroir il n'aurait vu que ceux du même sous-dossier, et les
    incoming_links seraient devenus silencieusement incomplets. Le tri par nom
    de document (et non par chemin) préserve l'ordre historique de la liste.

    :param out_root: Racine des sorties (output_files_preprocessing/)
    :param doc_name: Nom du document sans extension
    :return: Liste de liens entrants
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


# Markdown final — chemins portés par le workspace (lot F1)
def _resolve_markdown_file(workspace: DocumentWorkspace) -> Path | None:
    """
    Résout le fichier markdown à utiliser comme CONTENT (donc comme source de l'embedding).

    Préfère <doc>_final_embed.md (tables remplacées par du JSONL, produit par
    markdown_tables_to_jsonl.py --embed-output) s'il existe, sinon <doc>_final.md.
    Rétrocompatible : les documents sans _final_embed.md (v1/v2/baseline) sont inchangés.
    """
    for candidate in (workspace.final_embed_markdown, workspace.final_markdown):
        if candidate.exists():
            return candidate
    return None


def get_markdown_info(workspace: DocumentWorkspace) -> tuple[str, int]:
    """
    Retourne (content_filename, chunk_count).

    :param workspace: Workspace du document (porte tous les chemins)
    :return: Tuple (nom du fichier markdown, nombre de chunks)
    """
    resolved = _resolve_markdown_file(workspace)
    if resolved:
        return resolved.name, 1
    return workspace.final_markdown.name, 0


def get_markdown_content(workspace: DocumentWorkspace) -> str:
    """
    Retourne le contenu markdown du document — cf. _resolve_markdown_file pour
    la préférence _final_embed.md / _final.md. C'est ce texte qui devient la
    colonne CONTENT du CSV final ET la source de l'embedding.

    :param workspace: Workspace du document (porte tous les chemins)
    :return: Contenu markdown
    """
    resolved = _resolve_markdown_file(workspace)
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


# Construction du bloc metadata
def build_metadata(
    relative_doc_path: str,
    folder_source: Path,
    workspace: DocumentWorkspace,
    out_root: Path,
    embedding_model_name: str,
) -> dict:
    """
    Construit le bloc de metadata pour un document donné.

    Depuis le lot F1, tous les chemins du document viennent du ``workspace``
    (qui porte l'arborescence miroir) ; ``out_root`` ne sert plus qu'au
    balayage inter-documents des incoming_links (décision n°10).

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

        _log.info("Ligne ajoutée dans : %s", ws.final_csv)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
