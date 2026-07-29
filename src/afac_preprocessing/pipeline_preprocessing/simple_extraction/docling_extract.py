"""
Unified Docling pipeline — OCR + format export + table export in a single conversion.

Usage:
    uv run python docling_extract.py --input doc.pdf [options]

Replaces pipeline_multietape.py (stage1) + export_table_docling.py (stage2):
a single DocumentConverter.convert() call produces all requested formats.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

from ...utils.paths import project_root, resolve_doc_name, resolve_input_pdf
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
    TableStructureOptions,
)
from docling.document_converter import ConversionResult, DocumentConverter, PdfFormatOption

_log = logging.getLogger(__name__)

TEXT_FORMATS = frozenset({"json", "md", "txt", "doctags"})
TABLE_FORMATS = frozenset({"csv", "html"})

DEVICE_MAP: dict[str, AcceleratorDevice] = {
    "cuda": AcceleratorDevice.CUDA,
    "cpu": AcceleratorDevice.CPU,
    # "mps": AcceleratorDevice.MPS, # MPS (Apple Silicon)
}


# Build the converter, docling pipeline with configurable options
def build_converter(
    ocr: bool,
    lang: list[str],
    tables: bool,
    threads: int,
    device: AcceleratorDevice,
    extract_images: bool = False,
    images_scale: float = 2.0,
) -> DocumentConverter:
    """
    Configure the Docling converter with OCR, table, thread and device options.

    :param ocr: Whether to enable OCR (EasyOCR).
    :type ocr: bool
    :param lang: OCR language code(s) (EasyOCR).
    :type lang: list[str]
    :param tables: Whether to enable table structure detection.
    :type tables: bool
    :param threads: Number of CPU threads allocated to Docling.
    :type threads: int
    :param device: Hardware accelerator to use.
    :type device: AcceleratorDevice
    :param extract_images: Enables generate_picture_images to export PNGs via Docling.
    :type extract_images: bool
    :param images_scale: Scale factor for Docling images (base 72 DPI). E.g.: 2.08 ~= 150 DPI.
    :type images_scale: float
    :return: Configured DocumentConverter instance.
    :rtype: DocumentConverter
    """
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = tables
    if tables:
        opts.table_structure_options = TableStructureOptions(do_cell_matching=True)
    if ocr:
        opts.ocr_options = EasyOcrOptions(lang=lang)
    opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=device)
    if extract_images:
        opts.generate_picture_images = True
        opts.images_scale = images_scale
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def export_docling_images(conv_result, output_dir: Path) -> int:
    """Saves the images extracted by Docling (pil_image) as PNGs, named by their
    doctags coordinates (x0,y0,x1,y1) via pic.get_location_tokens(doc) — identical to those
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
            _log.warning("[docling] Image %d: provenance missing — export skipped", i)
            continue
        page = pic.prov[0].page_no
        # get_location_tokens() concatenates 4 <loc_*> per prov entry — an element that
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
    :type output_dir: Path
    :param formats: Set of text formats to export (subset of TEXT_FORMATS).
    :type formats: frozenset[str]
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
    (csv, html). Named <stem>-table-{i:02d}_page{page}_x{x0}_y{y0}_x{x1}_y{y1} — the
    coordinates (via table.get_location_tokens(doc), identical to those of the corresponding
    <otsl> tag in the doctags) allow load_jsonline_doctags.py to match each JSONL to the
    right <otsl> even if reordered_doctags.py has changed the relative order of the
    tables on a page. The {i:02d} index is only there for human readability of the
    folder — never used for downstream matching.

    :param conv_result: Docling conversion result to export.
    :param output_dir: Directory where exported files are written.
    :type output_dir: Path
    :param formats: Set of table formats to export (subset of TABLE_FORMATS).
    :type formats: frozenset[str]
    """
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
            df: pd.DataFrame = table.export_to_dataframe(doc=doc)
            path = tables_dir / f"{base_name}.csv"
            df.to_csv(path, index=False)
            _log.info("Table %d CSV: %s", i, path)

        if "html" in formats:
            path = tables_dir / f"{base_name}.html"
            path.write_text(table.export_to_html(doc=doc), encoding="utf-8")
            _log.info("Table %d HTML: %s", i, path)

    _log.info("%d table(s) exported to %s", len(tables), tables_dir)



# CLI (see README for usage examples)
def parse_args() -> argparse.Namespace:
    """
    Creates a command-line argument parser, allowing the user to specify the input file,
    the output directory, the formats to export, the OCR languages, the number of
    threads, and the hardware accelerator.

    :return: Parsed command-line arguments.
    :rtype: Namespace
    """
    parser = argparse.ArgumentParser(
        description=(
            "Docling pipeline: OCR + text format export + tables "
            "(a single conversion for all formats)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python docling_extract.py --input doc.pdf\n"
            "  uv run python docling_extract.py --input doc.pdf "
            "--formats doctags json\n"
            "  uv run python docling_extract.py --input doc.pdf "
            "--formats doctags --no-tables\n"
            "  uv run python docling_extract.py --input doc.pdf "
            "--no-ocr --formats json\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Path to the PDF to process. "
            "If absent, reads DOC_NAME from the environment and resolves "
            "data/input_files/<DOC_NAME>.pdf."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help=(
            "Output directory. "
            "Default: data/output_files_preprocessing/<doc_name>/ (relative to the project root)."
        ),
    )
    parser.add_argument(
        "--formats", "-f",
        nargs="+",
        default=sorted(TEXT_FORMATS),
        choices=sorted(TEXT_FORMATS),
        metavar="FORMAT",
        help=(
            f"Text formats to export among: {sorted(TEXT_FORMATS)}. "
            "Default: all (doctags json md txt). "
            "Table extraction (csv, html) is controlled separately via --no-tables."
        ),
    )
    parser.add_argument(
        "--lang", "-l",
        nargs="+",
        default=["fr"],
        metavar="LANG",
        help="EasyOCR language code(s) (e.g.: fr en). Default: fr.",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR (EasyOCR). Useful if the PDF contains native text.",
    )
    parser.add_argument(
        "--no-tables",
        action="store_true",
        help=(
            "Disable table extraction (csv + html). "
            "Also disables Docling structure detection to speed up conversion."
        ),
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=4,
        metavar="N",
        help="Number of CPU threads allocated to Docling. Default: 4.",
    )
    parser.add_argument(
        "--device",
        choices=sorted(DEVICE_MAP),
        default="cuda",
        help="Hardware accelerator. Default: cuda.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Path to a .env file to load before resolving DOC_NAME "
            "(e.g.: .env.test, .env). Ignored if --input is provided."
        ),
    )
    parser.add_argument(
        "--extract-images",
        action="store_true",
        default=False,
        help="Enables generate_picture_images to export Docling PNGs (used by description_image_context.py). Default: disabled.",
    )
    parser.add_argument(
        "--images-scale",
        type=float,
        default=2.08, # approximately 150 DPI (base 72 DPI) https://docling-project.github.io/docling/reference/pipeline_options/#docling.datamodel.pipeline_options.KserveV2OcrOptions.model_name
        metavar="F",
        help="Docling scale factor for images (base 72 DPI). E.g.: 2.08~=150dpi, 4.17~=300dpi. Default: 2.08.",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        metavar="FOLDER",
        help="Output directory for Docling PNGs. Default: used_images/ inside the output directory.",
    )
    return parser.parse_args()


# Path resolution
def resolve_input(args: argparse.Namespace) -> Path:
    """
    Checks and resolves the input PDF file path from the command-line arguments.
    If --input is provided, it is used directly. Otherwise, DOC_NAME is read from the
    environment (or from a .env file if --dotenv is provided).

    :param args: Parsed command-line arguments.
    :type args: argparse.Namespace
    :return: Resolved path to the input PDF file.
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    """
    Resolves the output directory from the command-line arguments, falling back to
    data/output_files_preprocessing/<doc_stem>/ under the project root.

    :param args: Parsed command-line arguments.
    :type args: argparse.Namespace
    :param input_path: Path to the resolved input PDF file.
    :type input_path: Path
    :return: Resolved output directory path.
    :rtype: Path
    """
    if args.output_dir:
        return args.output_dir.resolve()
    return project_root() / "data" / "output_files_preprocessing" / input_path.stem.strip()


# Entry point
def main() -> None:
    # Importing build_converter from another script no longer silently configures global logging.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_path = resolve_input(args)  # loads the dotenv if --dotenv is provided
    if not input_path.exists():
        raise SystemExit(f"Error: PDF file not found — {input_path}")

    output_dir = resolve_output(args, input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    text_fmts = frozenset(args.formats)
    do_table_structure = not args.no_tables
    do_table_export = not args.no_tables

    # --extract-images or ENABLE_IMAGE_EXTRACTION=true in the .env
    extract_images = args.extract_images or os.environ.get("ENABLE_IMAGE_EXTRACTION", "false").strip().lower() == "true"

    converter = build_converter(
        ocr=not args.no_ocr,
        lang=args.lang,
        tables=do_table_structure,
        threads=args.threads,
        device=DEVICE_MAP[args.device],
        extract_images=extract_images,
        images_scale=args.images_scale,
    )

    _log.info("Converting: %s", input_path)
    t0 = time.time()
    conv_result = converter.convert(input_path)
    _log.info("Conversion completed in %.2fs.", time.time() - t0)

    if text_fmts:
        export_text_formats(conv_result, output_dir, text_fmts)

    if do_table_export:
        export_tables(conv_result, output_dir, TABLE_FORMATS)

    if extract_images:
        images_dir = args.images_dir.resolve() if args.images_dir else output_dir / "used_images"
        export_docling_images(conv_result, images_dir)

    _log.info("Results in: %s", output_dir)
    sys.exit(0)  # Explicit exit code for Tekton


if __name__ == "__main__":
    main()
