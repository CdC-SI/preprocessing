"""docling-extract step, OCR + text exports + tables in a single conversion.

Conversion of the simple_extraction/docling_extract.py script.
Business functions are MOVED as-is, the only adaptation:
docling imports become LAZY (inside functions), so that Pipeline.default()
and afac-preprocess steps do not pay the ~10 s torch/docling import cost
without executing the step.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from docling.document_converter import ConversionResult, DocumentConverter

    from ..context import PipelineContext

_log = logging.getLogger(__name__)

TEXT_FORMATS = frozenset({"json", "md", "txt", "doctags"})
TABLE_FORMATS = frozenset({"csv", "html"})


# Build the converter, docling pipeline with configurable options
def build_converter(
    ocr: bool,
    lang: list[str],
    tables: bool,
    threads: int,
    device: str,
    extract_images: bool = False,
    images_scale: float = 2.0, # 2.0 because by default docling images are 72 DPI, and we want ~150 DPI (144 here) for the exported PNGs
) -> DocumentConverter:
    """
    Configure the Docling converter with OCR, table, thread and device options.

    (The body is identical to the script; device is passed by name, "cuda"/"cpu",
    and docling imports are local, see the module docstring.)

    :param ocr: Whether to enable OCR (EasyOCR).
    :param lang: OCR language code(s) (EasyOCR).
    :param tables: Whether to enable table structure detection.
    :param threads: Number of CPU threads allocated to Docling.
    :param device: Hardware accelerator to use ("cuda" or "cpu").
    :param extract_images: Enables generate_picture_images to export PNGs via Docling.
    :param images_scale: Scale factor for Docling images (base 72 DPI). E.g.: 2.08 ~= 150 DPI.
    :return: Configured DocumentConverter instance.
    """
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    device_map = {"cuda": AcceleratorDevice.CUDA, "cpu": AcceleratorDevice.CPU}

    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = tables
    if tables:
        opts.table_structure_options = TableStructureOptions(do_cell_matching=True)
    if ocr:
        opts.ocr_options = EasyOcrOptions(lang=lang)
    opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=device_map[device])
    if extract_images:
        opts.generate_picture_images = True
        opts.images_scale = images_scale
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def export_docling_images(conv_result: Any, output_dir: Path) -> int:
    """Saves the images extracted by Docling (pil_image) as PNGs, named by their
    doctags coordinates (x0,y0,x1,y1) via pic.get_location_tokens(doc),  identical to those
    of the corresponding <picture> tag in the doctags export. Naming by coordinates rather
    than positional index avoids misalignment if reordered_doctags.py later changes
    the relative order of images on a page (which is its very purpose): a match by positional
    index in the reordered doctags would then no longer point to the right file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = conv_result.document
    saved = 0
    for i, pic in enumerate(doc.pictures, start=1):
        img = getattr(pic, "image", None)
        pil = getattr(img, "pil_image", None) if img else None
        if pil is None:
            _log.warning("[docling] Image %d: pil_image missing (generate_picture_images enabled?)", i)
            continue
        if not pic.prov:
            _log.warning("[docling] Image %d: provenance missing, export skipped", i)
            continue
        page = pic.prov[0].page_no
        # get_location_tokens() concatenates 4 <loc_*> per prov entry, an element that
        # spans a page break has more than one. We only keep the first 4 (prov[0],
        # consistent with `page` above) to stay robust to multi-provenance images.
        x0, y0, x1, y1 = re.findall(r"<loc_(\d+)>", pic.get_location_tokens(doc))[:4]
        path = output_dir / f"pic_page{page}_x{x0}_y{y0}_x{x1}_y{y1}.png"
        pil.save(str(path))
        _log.info("[docling] Exported: %s", path.name)
        saved += 1
    _log.info("%d image(s) exported via Docling -> %s", saved, output_dir)
    return saved


# Export text formats, former function from pipeline_multietape.py (stage 1)
def export_text_formats(conv_result: ConversionResult, output_dir: Path, formats: frozenset[str]) -> None:
    """
    Export the requested text formats (json, md, txt, doctags) from the Docling conversion result.

    :param conv_result: Docling conversion result to export.
    :param output_dir: Directory where exported files are written.
    :param formats: Set of text formats to export (subset of TEXT_FORMATS).
    """
    stem = conv_result.input.file.stem.strip()
    doc = conv_result.document

    if "json" in formats:
        path = output_dir / f"{stem}.json"
        path.write_text(json.dumps(doc.export_to_dict(), ensure_ascii=False), encoding="utf-8")
        _log.info("JSON exported: %s", path)

    if "md" in formats:
        path = output_dir / f"{stem}.md"
        path.write_text(doc.export_to_markdown(), encoding="utf-8")
        _log.info("Markdown exported: %s", path)

    if "txt" in formats:
        path = output_dir / f"{stem}.txt"
        path.write_text(doc.export_to_text(), encoding="utf-8")
        _log.info("Plain text exported: %s", path)

    if "doctags" in formats:
        path = output_dir / f"{stem}.doctags"
        path.write_text(doc.export_to_doctags(), encoding="utf-8")
        _log.info("DocTags exported: %s", path)


# Export tables, former function from export_table_docling.py (stage 2)
def export_tables(conv_result: ConversionResult, output_dir: Path, formats: frozenset[str]) -> None:
    """
    Extracts and exports the tables detected in the Docling document to the requested formats
    (csv, html). Named <stem>-table-{i:02d}_page{page}_x{x0}_y{y0}_x{x1}_y{y1}, the
    coordinates (via table.get_location_tokens(doc), identical to those of the corresponding
    <otsl> tag in the doctags) allow load_jsonline_doctags.py to match each JSONL to the
    right <otsl> even if reordered_doctags.py has changed the relative order of the
    tables on a page. The {i:02d} index is only there for human readability of the
    folder — never used for downstream matching.

    :param conv_result: Docling conversion result to export.
    :param output_dir: Directory where exported files are written.
    :param formats: Set of table formats to export (subset of TABLE_FORMATS).
    """
    import pandas as pd  # noqa: F401  (import preserved: required by export_to_dataframe.)

    stem = conv_result.input.file.stem.strip()
    doc = conv_result.document
    tables = doc.tables

    if not tables:
        _log.info("No table detected in the document.")
        return

    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    for i, table in enumerate(tables, start=1):
        if not table.prov:
            _log.warning("Table %d: provenance missing — export skipped", i)
            continue
        page = table.prov[0].page_no
        # cf. export_docling_images(): only keep the first 4 <loc_*> (prov[0]) to
        # stay robust to tables whose multiple provs (page break) produce more.
        x0, y0, x1, y1 = re.findall(r"<loc_(\d+)>", table.get_location_tokens(doc))[:4]
        base_name = f"{stem}-table-{i:02d}_page{page}_x{x0}_y{y0}_x{x1}_y{y1}"

        if "csv" in formats:
            df = table.export_to_dataframe(doc=doc)
            path = tables_dir / f"{base_name}.csv"
            df.to_csv(path, index=False)
            _log.info("Table %d CSV: %s", i, path)

        if "html" in formats:
            path = tables_dir / f"{base_name}.html"
            path.write_text(table.export_to_html(doc=doc), encoding="utf-8")
            _log.info("Table %d HTML: %s", i, path)

    _log.info("%d table(s) exported to %s", len(tables), tables_dir)


class DoclingExtractStep(PipelineStep):
    """Conversion Docling du PDF : doctags, JSON, markdown, texte, tables, images."""

    name = "docling-extract"
    description = "Extraction Docling : doctags, JSON, texte, tables, images"
    requires_vlm = False

    def __init__(
        self,
        *,
        ocr: bool = True,
        lang: Sequence[str] = ("fr",),
        tables: bool = True,
        threads: int = 4,
        device: str = "cuda",
        images_scale: float = 2.0,  # 144 DPI (base 72)
    ) -> None:
        # Defaults identical to the historical script's `parse_args()`.
        self.ocr = ocr
        self.lang = list(lang)
        self.tables = tables
        self.threads = threads
        self.device = device
        self.images_scale = images_scale

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.source_pdf]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        ws = ctx.workspace
        return [ws.doctags, ws.docling_json, ws.markdown, ws.text_dump,
                ws.tables_dir, ws.used_images_dir]

    def execute(self, ctx: PipelineContext) -> StepResult:
        input_path = ctx.workspace.source_pdf
        output_dir = ctx.workspace.root
        output_dir.mkdir(parents=True, exist_ok=True)

        extract_images = ctx.settings.enable_image_extraction

        try:
            converter = build_converter(
                ocr=self.ocr,
                lang=self.lang,
                tables=self.tables,
                threads=self.threads,
                device=self.device,
                extract_images=extract_images,
                images_scale=self.images_scale,
            )

            _log.info("Converting: %s", input_path)
            t0 = time.time()
            conv_result = converter.convert(input_path)
            _log.info("Conversion completed in %.2fs.", time.time() - t0)

            export_text_formats(conv_result, output_dir, TEXT_FORMATS)
            if self.tables:
                export_tables(conv_result, output_dir, TABLE_FORMATS)
            if extract_images:
                export_docling_images(conv_result, ctx.workspace.used_images_dir)
        except Exception as exc:
            _log.exception("docling-extract failed on %s", input_path.name)
            raise StepFailed(f"docling-extract failed on {input_path.name}: {exc}") from exc

        _log.info("Results in: %s", output_dir)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
