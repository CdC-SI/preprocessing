import argparse
import base64
import logging
import os
import queue
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
import fitz  # PyMuPDF
from openai import OpenAI

from ...prompts.prompts import WIKI_PROMPT_TEMPLATE
from ...utils.paths import project_root, resolve_input_pdf
from ...utils.vlm_client import (
    build_sync_client,
    build_vlm_config,
    check_vlm_connectivity,
    vision_completion,
)

_log = logging.getLogger(__name__)


# Constants
NORM = 500
DPI_DEFAULT = 150
N_BEFORE = 5
N_AFTER = 5

TEXT_TAGS = {
    "text", "section_header_level_1", "section_header_level_2",
    "section_header_level_3", "list_item", "caption", "footnote",
    "page_header", "page_footer",
}

# Data models and classes
class PictureTag(TypedDict):
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    raw_tag: str


class DocElement(TypedDict):
    type: str   # "text" | "picture" | "other"
    tag: str
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    text: str


class VLMResult(TypedDict):
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    description: str
    raw_tag: str


@dataclass
class ImageTask:
    index: int
    total: int
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    image_b64: str
    prompt: str
    raw_tag: str


# Business logic (pure functions / no global state)
def remove_picture_tags(content: str) -> str:
    """
    Remove <picture> tags when image description is disabled.

    :param content: The doctags content to process.
    :type content: str
    :return: The content with all <picture> tags removed.
    :rtype: str
    """
    return re.sub(r'<picture><loc_\d+><loc_\d+><loc_\d+><loc_\d+></picture>', '', content)


def parse_picture_tags(content: str) -> list[PictureTag]:
    """
    Parse <picture> tags from the doctags content. Returns the list of pictures found.

    :param content: The doctags content to parse.
    :type content: str
    :return: The list of picture tags found, in document order.
    :rtype: list[PictureTag]
    """
    pictures = []
    page = 0

    for line in content.splitlines():
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1
        for m in re.finditer(
            r"(<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>)",
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            pictures.append({
                "page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "raw_tag": m.group(1),
            })
            _log.debug("<picture> found page=%d loc=(%d,%d,%d,%d)", page, x0, y0, x1, y1)

    _log.info("%d <picture> tag(s) found", len(pictures))
    return pictures


def extract_document_elements(content: str) -> list[DocElement]:
    """
    Parse all elements (text + images) in their order of appearance.

    :param content: The doctags content to parse.
    :type content: str
    :return: The list of document elements, in document order.
    :rtype: list[DocElement]
    """
    elements = []
    page = 0

    for line in content.splitlines():
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1

        for m in re.finditer(
            r"<(?!/)(\w+)><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>([^<]*)",
            line_clean, re.DOTALL,
        ):
            tag = m.group(1)
            if tag == "picture":
                continue
            x0, y0, x1, y1 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            raw_text = re.sub(r"<[^>]+>", "", m.group(6)).strip()
            elements.append({
                "type": "text" if tag in TEXT_TAGS else "other",
                "tag": tag, "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "text": raw_text,
            })

        for m in re.finditer(
            r"<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>",
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            elements.append({
                "type": "picture", "tag": "picture", "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
            })

    _log.info("%d total element(s) parsed from the doctags", len(elements))
    return elements


def build_context(pic: PictureTag, doc_elements: list[DocElement], n_before: int, n_after: int) -> tuple[str, str]:
    """
    Return the textual context (ctx_before, ctx_after) surrounding an image.

    :param pic: The picture tag to build context for.
    :type pic: PictureTag
    :param doc_elements: The full list of document elements to search within.
    :type doc_elements: list[DocElement]
    :param n_before: Number of text elements to include before the image.
    :type n_before: int
    :param n_after: Number of text elements to include after the image.
    :type n_after: int
    :return: A tuple of (context before, context after) as formatted strings.
    :rtype: tuple[str, str]
    """
    pic_index = next((
        idx for idx, e in enumerate(doc_elements)
        if e["type"] == "picture"
        and e["page"] == pic["page"]
        and e["x0"] == pic["x0"]
        and e["y0"] == pic["y0"]
    ), None)

    if pic_index is not None:
        before = [e["text"] for e in doc_elements[:pic_index] if e["type"] == "text" and e["text"]][-n_before:]
        after = [e["text"] for e in doc_elements[pic_index + 1:] if e["type"] == "text" and e["text"]][:n_after]
    else:
        before, after = [], []

    ctx_before = "\n".join(f"> {t}" for t in before) if before else "> No context available before this image."
    ctx_after = "\n".join(f"> {t}" for t in after) if after else "> No context available after this image."
    return ctx_before, ctx_after


def _clip_rect(page: fitz.Page, pic: PictureTag, norm: int) -> fitz.Rect:
    """Convert normalized DocTags coordinates into a PyMuPDF Rect for cropping."""
    pw, ph = page.rect.width, page.rect.height
    return fitz.Rect(
        pic["x0"] / norm * pw, pic["y0"] / norm * ph,
        pic["x1"] / norm * pw, pic["y1"] / norm * ph,
    )


def load_preextracted_b64(images_dir: Path, pic: PictureTag) -> str | None:
    """Load an image pre-extracted by Docling from disk, matched by coordinates
    (x0,y0,x1,y1) — never by positional index, so it stays correct even if
    reordered_doctags.py has changed the relative order of images on the page. The
    file name is produced by docling_extract.export_docling_images() with the same
    coordinates (cf. pic.get_location_tokens(doc), identical to what the <picture> tag exports).

    Backward compatibility: a used_images/ folder generated by a run predating this
    coordinate-based naming contains files named `pic{i:03d}_page{p}.png` (by positional
    index). Fall back to this naming only if a single image exists for this page in
    the folder — beyond that, the positional index cannot be reliably recovered here
    (this is precisely the ambiguity that coordinate-based naming eliminates); it's better to
    fall back to the fitz crop (see caller) than risk matching the wrong image."""
    page = pic["page"] + 1
    path = images_dir / f"pic_page{page}_x{pic['x0']}_y{pic['y0']}_x{pic['x1']}_y{pic['y1']}.png"
    if not path.exists():
        legacy_matches = sorted(images_dir.glob(f"pic*_page{page}.png"))
        if len(legacy_matches) == 1:
            _log.info("Pre-extracted image found via legacy naming (index): %s", legacy_matches[0].name)
            path = legacy_matches[0]
        else:
            _log.warning("Pre-extracted image not found: %s", path)
            return None
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def crop_to_b64(pdf_doc: fitz.Document, pic: PictureTag, norm: int = NORM, dpi: int = DPI_DEFAULT) -> str:
    """
    Crop an image out of the PDF and return the PNG as base64, in memory.

    :param pdf_doc: The open PDF document to crop from.
    :type pdf_doc: fitz.Document
    :param pic: The picture tag describing the crop region.
    :type pic: PictureTag
    :param norm: Normalization factor for the DocTags coordinate system.
    :type norm: int
    :param dpi: DPI resolution used to render the crop.
    :type dpi: int
    :return: The cropped image, PNG-encoded and base64-encoded.
    :rtype: str
    """
    page = pdf_doc[pic["page"]]
    pix = page.get_pixmap(dpi=dpi, clip=_clip_rect(page, pic, norm))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def describe_image_b64(image_b64: str, prompt: str, client: OpenAI, model_name: str) -> str:
    """Send the image (base64) + prompt to the VLM and return the description.

    Cache-first + retry built into the SDK (cf. utils.vlm_client.vision_completion) — the manual
    retry logic and hand-built payload were removed when consolidating onto a single
    OpenAI client.
    """
    try:
        return vision_completion(client, model_name, prompt, image_b64, max_tokens=3000)
    except Exception:
        _log.exception("VLM API error")
        return ""


def export_picture_images(
    pdf_path: Path,
    pictures: list[PictureTag],
    doc_name: str,
    output_dir: Path,
    norm: int = NORM,
    dpi: int = DPI_DEFAULT,
) -> None:
    """
    Export a PNG for each <picture> tag, named after their doctags coordinates.
    :param pdf_path: Path to the source PDF.
    :type pdf_path: Path
    :param pictures: The list of picture tags to export.
    :type pictures: list[PictureTag]
    :param doc_name: Name of the document (used in the exported file names).
    :type doc_name: str
    :param output_dir: Directory where the exported PNGs are written.
    :type output_dir: Path
    :param norm: Normalization factor for the DocTags coordinate system.
    :type norm: int
    :param dpi: DPI resolution used to render the crops.
    :type dpi: int
    """
    _log.info("Exporting PNGs to: %s", output_dir)
    with fitz.open(str(pdf_path)) as doc:
        for i, pic in enumerate(pictures, start=1):
            page = doc[pic["page"]]
            pix = page.get_pixmap(dpi=dpi, clip=_clip_rect(page, pic, norm))
            img_path = output_dir / (
                f"{doc_name}_page{pic['page'] + 1}_"
                f"x{pic['x0']}_y{pic['y0']}_x{pic['x1']}_y{pic['y1']}.png"
            )
            img_path.write_bytes(pix.tobytes("png"))
            _log.info("[%d/%d] PNG exported: %s", i, len(pictures), img_path.name)

    _log.info("%d PNG(s) exported", len(pictures))


def _vlm_worker(
    task_queue: queue.Queue,
    results: dict[int, VLMResult],
    results_lock: threading.Lock,
    client: OpenAI,
    model_name: str,
) -> None:
    """Consumer thread: calls the VLM and stores the results in results.

    The sync OpenAI client is thread-safe for concurrent use (it wraps an underlying
    httpx.Client connection pool) — a single instance is shared across all workers.
    """
    while True:
        task: ImageTask | None = task_queue.get()
        if task is None:
            break

        _log.info(
            "[%d/%d] -> VLM — Page %d loc=(%d,%d,%d,%d)",
            task.index, task.total, task.page + 1,
            task.x0, task.y0, task.x1, task.y1,
        )
        description = describe_image_b64(task.image_b64, task.prompt, client, model_name)

        with results_lock:
            results[task.index] = VLMResult(
                page=task.page,
                x0=task.x0, y0=task.y0,
                x1=task.x1, y1=task.y1,
                description=description,
                raw_tag=task.raw_tag,
            )

        if description:
            _log.info("[%d/%d] Description received (%d chars)", task.index, task.total, len(description))
        else:
            _log.warning("[%d/%d] No description returned by the VLM", task.index, task.total)

        task_queue.task_done()


def describe_all_pictures(
    pdf_path: Path,
    pictures: list[PictureTag],
    doc_elements: list[DocElement],
    client: OpenAI,
    model_name: str,
    language: str = "french",
    n_before: int = N_BEFORE,
    n_after: int = N_AFTER,
    n_workers: int = 1,
    dpi: int = DPI_DEFAULT,
    norm: int = NORM,
    preextracted_images_dir: Path | None = None,
) -> dict[int, VLMResult]:
    """
    Crop each image (or load it from preextracted_images_dir), build the contextualized prompt, and send it to the VLM.
    Returns a dict indexed 1-based: {index: VLMResult}.

    :param pdf_path: Path to the source PDF.
    :type pdf_path: Path
    :param pictures: The list of picture tags to describe.
    :type pictures: list[PictureTag]
    :param doc_elements: The full list of document elements, used to build context.
    :type doc_elements: list[DocElement]
    :param client: Configured OpenAI client (shared across all workers).
    :type client: OpenAI
    :param model_name: Name of the VLM model.
    :type model_name: str
    :param language: Language the VLM should respond in.
    :type language: str
    :param n_before: Number of text elements to include before each image for context.
    :type n_before: int
    :param n_after: Number of text elements to include after each image for context.
    :type n_after: int
    :param n_workers: Number of parallel VLM worker threads.
    :type n_workers: int
    :param preextracted_images_dir: Folder containing PNGs pre-extracted by Docling (pic{i:03d}_page{p}.png).
    :type preextracted_images_dir: Path | None
    :return: A dict mapping each 1-based image index to its VLMResult.
    :rtype: dict[int, VLMResult]
    """
    results: dict[int, VLMResult] = {}
    results_lock = threading.Lock()
    total = len(pictures)
    task_q: queue.Queue = queue.Queue()

    workers = [
        threading.Thread(target=_vlm_worker, args=(task_q, results, results_lock, client, model_name), daemon=True)
        for _ in range(n_workers)
    ]
    for w in workers:
        w.start()

    if preextracted_images_dir:
        _log.info("Queuing %d image(s) — source: %s — %d worker(s)", total, preextracted_images_dir, n_workers)
    else:
        _log.info("Queuing %d image(s) — source: fitz crop — %d worker(s)", total, n_workers)

    with fitz.open(str(pdf_path)) as pdf_doc:
        for i, pic in enumerate(pictures, start=1):
            ctx_before, ctx_after = build_context(pic, doc_elements, n_before, n_after)
            prompt = WIKI_PROMPT_TEMPLATE.format(
                context_before=ctx_before,
                context_after=ctx_after,
                language=language,
            )
            if preextracted_images_dir:
                image_b64 = load_preextracted_b64(preextracted_images_dir, pic)
                if image_b64 is None:
                    _log.warning("[%d/%d] Pre-extracted image missing — falling back to fitz crop", i, total)
                    image_b64 = crop_to_b64(pdf_doc, pic, norm=norm, dpi=dpi)
            else:
                image_b64 = crop_to_b64(pdf_doc, pic, norm=norm, dpi=dpi)
            task_q.put(ImageTask(
                index=i, total=total,
                page=pic["page"],
                x0=pic["x0"], y0=pic["y0"],
                x1=pic["x1"], y1=pic["y1"],
                image_b64=image_b64,
                prompt=prompt,
                raw_tag=pic["raw_tag"],
            ))

    task_q.join()          # wait until all tasks have been processed
    for _ in workers:      # then send the stop sentinels
        task_q.put(None)
    for w in workers:
        w.join()

    described = sum(1 for r in results.values() if r["description"])
    _log.info("%d/%d image(s) described with context", described, total)
    return results


def replace_picture_tags(content: str, results: dict[int, VLMResult]) -> str:
    """
    Replace <picture> tags with [[[IMAGE_DESC:N]]] markers in the doctags content.
    The actual descriptions are injected after the VLM checkpoint (stage 4) by
    inject_image_descriptions.py, guaranteeing they cannot be
    removed or altered by intermediate VLM steps.

    :param content: The doctags content to process.
    :type content: str
    :param results: The VLM results indexed by 1-based image index.
    :type results: dict[int, VLMResult]
    :return: The content with <picture> tags replaced by placeholders.
    :rtype: str
    """
    replaced = 0
    for idx in sorted(results.keys()):
        r = results[idx]
        if not r["description"]:
            _log.warning("No description for <picture> idx=%d — tag kept as-is", idx)
            continue
        if r["raw_tag"] not in content:
            _log.error("raw_tag not found: %s", r["raw_tag"])
            continue

        placeholder = f"[[[IMAGE_DESC:{idx}]]]"
        raw_tag_escaped = re.escape(r["raw_tag"])

        pattern_in_item = re.compile(r"<list_item>\s*" + raw_tag_escaped + r"\s*</list_item>", re.DOTALL)
        if pattern_in_item.search(content):
            content = pattern_in_item.sub(f"<list_item>{placeholder}</list_item>", content, count=1)
            _log.info("[%d] <picture> inside <list_item> -> inline placeholder", idx)
        elif re.search(r"<list_item[^>]*>.*?</list_item>\s*" + raw_tag_escaped, content, re.DOTALL):
            content = content.replace(r["raw_tag"], f"<list_item>{placeholder}</list_item>", 1)
            _log.info("[%d] <picture> between list_items -> <list_item> placeholder", idx)
        else:
            content = content.replace(r["raw_tag"], f"<text>{placeholder}</text>", 1)
            _log.info("[%d] <picture> standalone -> <text> placeholder", idx)

        replaced += 1

    _log.info("%d/%d <picture> tag(s) replaced with a placeholder", replaced, len(results))
    return content


def export_descriptions_to_markdown(
    results: dict[int, VLMResult],
    doc_name: str,
    output_path: Path,
    vlm_model_name: str,
) -> None:
    """
    Export the VLM descriptions to a reference Markdown file.

    :param results: The VLM results indexed by 1-based image index.
    :type results: dict[int, VLMResult]
    :param doc_name: Name of the document (used in the Markdown title).
    :type doc_name: str
    :param output_path: Path to the Markdown file to write.
    :type output_path: Path
    :param vlm_model_name: Name of the VLM model used, shown in the report header.
    :type vlm_model_name: str
    """
    total = len(results)
    nb_described = sum(1 for r in results.values() if r["description"])
    nb_missing = total - nb_described
    sections = []

    for i in sorted(results.keys()):
        r = results[i]
        loc_str = f"loc({r['x0']}, {r['y0']}, {r['x1']}, {r['y1']})"
        page_str = f"Page {r['page'] + 1}"
        if r["description"]:
            sections.append(
                f"## OK - Image {i}/{total} — {page_str} | `{loc_str}`\n\n{r['description']}\n"
            )
        else:
            sections.append(
                f"## WARNING - Image {i}/{total} — {page_str} | `{loc_str}`\n\n"
                f"> **No description generated.**\n"
                f"> *Check the coordinates or the VLM response.*\n"
            )

    header = (
        f"# Image descriptions — *{doc_name}*\n\n"
        f"> Automatically generated by the VLM pipeline  \n"
        f"> Source document: `{doc_name}.pdf`  \n"
        f"> Number of images detected: **{total}**  \n"
        f"> VLM model: `{vlm_model_name}`\n\n---\n\n"
    )
    summary = (
        f"## Summary\n\n"
        f"- Images detected  : **{total}**\n"
        f"- Images described : **{nb_described}**\n"
        f"- Images missing   : **{nb_missing}**\n"
    )
    output_path.write_text(
        header + "\n\n---\n\n".join(sections) + "\n\n---\n\n" + summary,
        encoding="utf-8",
    )
    _log.info("Markdown exported (%d/%d images described): %s", nb_described, total, output_path)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Describes the images in a .doctags file via VLM with textual context. "
            "Replaces <picture> tags with the generated descriptions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python description_image_context_modulaire.py \\\n"
            "      --doctags data/output_files_preprocessing/MonDoc/MonDoc_reordered_with_tables.doctags \\\n"
            "      --input   data/input_files/MonDoc.pdf \\\n"
            "      --image-description\n"
            "  uv run python description_image_context_modulaire.py --dotenv .env.test\n"
            "  uv run python description_image_context_modulaire.py --dotenv .env.test --no-image-description\n"
        ),
    )
    parser.add_argument(
        "--doctags", "-d",
        type=Path, default=None,
        help=(
            "Source .doctags file (produced by load_jsonline_doctags_modulaire.py). "
            "If omitted, resolves to data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path, default=None,
        help="Source PDF file used to crop the images. If omitted, resolves to data/input_files/<DOC_NAME>.pdf.",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path, default=None,
        help="Output enriched .doctags file. Default: <stem>_pictures.doctags in the same folder.",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path, default=None,
        help="Output Markdown file for the descriptions. Default: <doc_name>_image_descriptions.md.",
    )
    parser.add_argument(
        "--images-dir",
        type=Path, default=None,
        help="Output folder for the exported PNGs. Default: used_images/ inside the --doctags folder.",
    )
    parser.add_argument(
        "--doc-name",
        type=str, default=None,
        help="Document name (used in logs and Markdown). If omitted, inferred from DOC_NAME or from the --doctags file name.",
    )
    parser.add_argument(
        "--language",
        type=str, default="french",
        help="Language of the VLM response. Default: french.",
    )
    parser.add_argument(
        "--image-description",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable (--image-description) or disable (--no-image-description) VLM description, "
            "taking priority over ENABLE_IMAGE_DESCRIPTION from the .env. If omitted, falls back to "
            "ENABLE_IMAGE_DESCRIPTION (false if absent from the .env)."
        ),
    )
    parser.add_argument(
        "--workers", "-w",
        type=int, default=1, metavar="N",
        help="Number of parallel VLM threads. Default: 1 (sequential, safe for rate-limited APIs).",
    )
    parser.add_argument(
        "--timeout",
        type=int, default=120, metavar="SEC",
        help="Timeout in seconds for each VLM call. Default: 120.",
    )
    parser.add_argument(
        "--dpi",
        type=int, default=DPI_DEFAULT, metavar="N",
        help=f"DPI resolution used to crop PDF images. Default: {DPI_DEFAULT}.",
    )
    parser.add_argument(
        "--n-before",
        type=int, default=N_BEFORE, metavar="N",
        help=f"Number of text elements before the image to include as VLM context. Default: {N_BEFORE}.",
    )
    parser.add_argument(
        "--n-after",
        type=int, default=N_AFTER, metavar="N",
        help=f"Number of text elements after the image to include as VLM context. Default: {N_AFTER}.",
    )
    parser.add_argument(
        "--norm",
        type=int, default=NORM, metavar="N",
        help=f"Normalization factor for DocTags coordinates (the .doctags coordinate system). Default: {NORM}.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path, default=None, metavar="FILE",
        help="The .env file to load (VLM_URL, VLM_CA_PEM, VLM_MODEL_NAME, DOC_NAME). Always loaded for VLM config; if absent, variables are read from the environment.",
    )
    parser.add_argument(
        "--preextracted-images-dir",
        type=Path, default=None, metavar="FOLDER",
        help=(
            "Folder containing PNGs pre-extracted by docling_extract.py --extract-images "
            "(named pic{i:03d}_page{p}.png). If provided, replaces the fitz crop. "
            "Default: None (fitz used)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level. Default: INFO. Pass DEBUG to diagnose a Tekton step.",
    )
    return parser.parse_args()


# Path resolution
def _load_doc_name(args: argparse.Namespace) -> str:
    """
    Load DOC_NAME from --dotenv or the environment. Raises SystemExit if absent.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :return: The resolved document name.
    :rtype: str
    """
    if args.dotenv and not Path(args.dotenv).resolve().exists():
        raise SystemExit(f"Error: .env file not found — {Path(args.dotenv).resolve()}")
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Error: provide --doctags and --pdf, or --dotenv <file> with DOC_NAME, "
            "or set the DOC_NAME environment variable."
        )
    return doc_name


def resolve_doctags(args: argparse.Namespace) -> Path:
    """
    Resolve the path to the source .doctags file.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :return: The resolved .doctags path.
    :rtype: Path
    """
    if args.doctags:
        return args.doctags.resolve()
    doc_name = _load_doc_name(args)
    return project_root() / "data" / "output_files_preprocessing" / doc_name / f"{doc_name}_reordered_with_tables.doctags"


def resolve_pdf(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Resolve the path to the source PDF file.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param doctags_path: Path to the resolved .doctags file, used to infer the document name.
    :type doctags_path: Path
    :return: The resolved PDF path.
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    env_name = os.environ.get("DOC_NAME", "").strip()
    doc_name = env_name or doctags_path.stem.split("_")[0]
    return resolve_input_pdf(doc_name)


def _resolve_image_doc_name(args: argparse.Namespace, doctags_path: Path) -> str:
    """
    Resolve the document name used for logs and the Markdown output.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param doctags_path: Path to the resolved .doctags file, used as a fallback source for the name.
    :type doctags_path: Path
    :return: The resolved document name.
    :rtype: str
    """
    if args.doc_name:
        return args.doc_name
    env_name = os.environ.get("DOC_NAME", "").strip()
    if env_name:
        return env_name
    return doctags_path.stem.split("_")[0]


def resolve_output(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Resolve the path to the enriched output .doctags file.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param doctags_path: Path to the source .doctags file, used to derive the default output path.
    :type doctags_path: Path
    :return: The resolved output path.
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    return doctags_path.parent / f"{doctags_path.stem}_pictures.doctags"


def resolve_markdown(args: argparse.Namespace, doctags_path: Path, doc_name: str) -> Path:
    """
    Resolve the path to the output Markdown file for the descriptions.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param doctags_path: Path to the source .doctags file, used to derive the default output folder.
    :type doctags_path: Path
    :param doc_name: Document name, used to build the default file name.
    :type doc_name: str
    :return: The resolved Markdown path.
    :rtype: Path
    """
    if args.markdown:
        return args.markdown.resolve()
    return doctags_path.parent / f"{doc_name}_image_descriptions.md"


def resolve_images_dir(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Resolve the output folder for exported PNGs.

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param doctags_path: Path to the source .doctags file, used to derive the default folder.
    :type doctags_path: Path
    :return: The resolved images folder path.
    :rtype: Path
    """
    if args.images_dir:
        return args.images_dir.resolve()
    return doctags_path.parent / "used_images"


# Entry point
def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # VLM config — loaded via build_vlm_config (dotenv + CA certifi)
    # The dotenv is loaded HERE, so ENABLE_IMAGE_DESCRIPTION is readable afterwards.
    try:
        vlm_cfg = build_vlm_config(dotenv_path=args.dotenv)
    except RuntimeError as exc:
        # dotenv loaded but VLM_URL missing — check whether description is actually required
        env_enabled = os.environ.get("ENABLE_IMAGE_DESCRIPTION", "false").strip().lower() == "true"
        needs_vlm = args.image_description if args.image_description is not None else env_enabled
        if needs_vlm:
            raise SystemExit(str(exc)) from exc
        vlm_cfg = None

    # --image-description/--no-image-description takes priority; without an explicit flag,
    # falls back to ENABLE_IMAGE_DESCRIPTION from the .env (loaded above).
    if args.image_description is not None:
        image_desc_enabled = args.image_description
    else:
        image_desc_enabled = os.environ.get("ENABLE_IMAGE_DESCRIPTION", "false").strip().lower() == "true"

    model_name = vlm_cfg.vlm_model_name if vlm_cfg else ""
    client = build_sync_client(vlm_cfg, timeout=args.timeout) if vlm_cfg else None

    # Path resolution (DOC_NAME already loaded via load_vlm_config)
    doctags_path = resolve_doctags(args)
    if not doctags_path.exists():
        raise SystemExit(f"Error: .doctags file not found — {doctags_path}")

    pdf_path = resolve_pdf(args, doctags_path)
    if not pdf_path.exists():
        raise SystemExit(f"Error: PDF file not found — {pdf_path}")

    doc_name = _resolve_image_doc_name(args, doctags_path)
    output_path = resolve_output(args, doctags_path)
    markdown_path = resolve_markdown(args, doctags_path, doc_name)
    images_dir = resolve_images_dir(args, doctags_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("Doctags source   : %s", doctags_path)
    _log.info("PDF source       : %s", pdf_path)
    _log.info("Doctags output   : %s", output_path)
    _log.info("Markdown         : %s", markdown_path)
    _log.info("PNG images       : %s", images_dir)
    _log.info(
        "VLM descriptions : %s for '%s'",
        "ENABLED" if image_desc_enabled else "DISABLED",
        doc_name,
    )

    # Step 1 — Parsing (single read of the file)
    _log.info("STEP 1 — Parsing <picture> tags + doctags elements")
    content = doctags_path.read_text(encoding="utf-8")
    pictures = parse_picture_tags(content)
    doc_elements = extract_document_elements(content)

    if not pictures:
        _log.warning(
            "No <picture> tag found — doctags copied unmodified, "
            "no descriptions file created."
        )
        output_path.write_text(content, encoding="utf-8")
        sys.exit(0)

    # Step 2 — Export PNGs via fitz (skipped if pre-extracted images are available)
    # Resolution of preextracted_dir:
    #   1. explicit --preextracted-images-dir
    #   2. used_images/ inside the doctags folder if it exists (generated by pipeline_multietape --extract-images)
    #   3. None -> fitz crop
    if args.preextracted_images_dir:
        preextracted_dir = args.preextracted_images_dir.resolve()
    elif images_dir.exists() and any(images_dir.glob("pic*.png")):
        preextracted_dir = images_dir
        _log.info("Pre-extracted images automatically detected: %s", preextracted_dir)
    else:
        preextracted_dir = None

    if preextracted_dir:
        _log.info("STEP 2 — Pre-extracted images: %s (fitz crop skipped)", preextracted_dir)
    else:
        _log.info("STEP 2 — Exporting PNG images (doctags coordinates via fitz)")
        images_dir.mkdir(parents=True, exist_ok=True)
        export_picture_images(pdf_path, pictures, doc_name, images_dir, dpi=args.dpi, norm=args.norm)

    # Step 3 — VLM description (or removal if disabled)
    _log.info("STEP 3 — Describing images with textual context")
    if not image_desc_enabled:
        _log.warning(
            "VLM descriptions disabled for '%s' — <picture> tags removed, "
            "no descriptions file created.",
            doc_name,
        )
        output_path.write_text(remove_picture_tags(content), encoding="utf-8")
        sys.exit(0)

    if not model_name:
        raise SystemExit(
            "Error: VLM_MODEL_NAME not set. "
            "Provide --dotenv <file> or set VLM_MODEL_NAME in the environment."
        )

    if not check_vlm_connectivity(client, model_name):
        _log.error("Aborting — VLM unreachable. Check VLM_URL and VLM_CA_PEM.")
        sys.exit(1)

    try:
        results = describe_all_pictures(
            pdf_path, pictures, doc_elements, client, model_name,
            language=args.language,
            n_workers=args.workers,
            n_before=args.n_before,
            n_after=args.n_after,
            dpi=args.dpi,
            norm=args.norm,
            preextracted_images_dir=preextracted_dir,
        )
    except Exception:
        _log.exception("Error while describing images for '%s'", doc_name)
        sys.exit(1)

    # Step 4 — Replacing <picture> tags
    _log.info("STEP 4 — Replacing <picture> tags in the doctags")
    output_path.write_text(replace_picture_tags(content, results), encoding="utf-8")
    _log.info("Enriched doctags saved: %s", output_path)

    # Step 5 — Markdown export
    _log.info("STEP 5 — Exporting descriptions to Markdown")
    export_descriptions_to_markdown(results, doc_name, markdown_path, model_name)

    sys.exit(0)


if __name__ == "__main__":
    main()
    