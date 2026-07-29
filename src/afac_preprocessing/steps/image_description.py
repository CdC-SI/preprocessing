"""Étape image-description — description des images du doctags via VLM.

Conversion de ``description_image/description_image_context.py`` (vague D).

⚠ SEULE EXCEPTION AUTORISÉE à l'invariant n°1 (piège P5) : le script n'était
pas « sync », c'était un pool de threads (``queue.Queue`` + N
``threading.Thread`` + ``Lock``). La contrainte C2 impose son remplacement
par ``asyncio.Semaphore`` + ``asyncio.gather``, en copiant la structure de
``url_tuning`` (vague C). La réécriture est LIMITÉE AU DISPATCH :

| Avant (threads)                     | Après (async)                        |
|-------------------------------------|--------------------------------------|
| task_q: queue.Queue + ImageTask     | liste de coroutines pour gather      |
| N × threading.Thread(_vlm_worker)   | asyncio.Semaphore(n) autour du call  |
| results_lock autour des écritures   | plus de verrou (une tâche à la fois) |
| sentinelles None pour arrêter       | gather se termine tout seul          |
| vision_completion (sync)            | await vlm.vision_completion(...)     |
| flag CLI --workers                  | attribut max_concurrency (défaut 1)  |

L'ordre des descriptions suit TOUJOURS l'ordre des images (résultats indexés
par ``task.index``, consommés via ``sorted(results)``) — pas l'ordre
d'arrivée des réponses.

Toutes les fonctions d'extraction, de contexte et d'écriture sont DÉPLACÉES
telles quelles.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..prompts.prompts import WIKI_PROMPT_TEMPLATE

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..context import PipelineContext

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


# Business logic (pure functions / no global state) — déplacées telles quelles
def remove_picture_tags(content: str) -> str:
    """
    Remove <picture> tags when image description is disabled.

    :param content: The doctags content to process.
    :return: The content with all <picture> tags removed.
    """
    return re.sub(r'<picture><loc_\d+><loc_\d+><loc_\d+><loc_\d+></picture>', '', content)


def parse_picture_tags(content: str) -> list[PictureTag]:
    """
    Parse <picture> tags from the doctags content. Returns the list of pictures found.

    :param content: The doctags content to parse.
    :return: The list of picture tags found, in document order.
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
    :return: The list of document elements, in document order.
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
    :param doc_elements: The full list of document elements to search within.
    :param n_before: Number of text elements to include before the image.
    :param n_after: Number of text elements to include after the image.
    :return: A tuple of (context before, context after) as formatted strings.
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
    :param pic: The picture tag describing the crop region.
    :param norm: Normalization factor for the DocTags coordinate system.
    :param dpi: DPI resolution used to render the crop.
    :return: The cropped image, PNG-encoded and base64-encoded.
    """
    page = pdf_doc[pic["page"]]
    pix = page.get_pixmap(dpi=dpi, clip=_clip_rect(page, pic, norm))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


async def describe_image_b64(image_b64: str, prompt: str, vlm: AsyncVlmClient) -> str:
    """Send the image (base64) + prompt to the VLM and return the description.

    (Async depuis la vague D — même contrat : retourne "" en cas d'erreur.)
    """
    try:
        return await vlm.vision_completion(prompt, image_b64, max_tokens=3000)
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
    :param pictures: The list of picture tags to export.
    :param doc_name: Name of the document (used in the exported file names).
    :param output_dir: Directory where the exported PNGs are written.
    :param norm: Normalization factor for the DocTags coordinate system.
    :param dpi: DPI resolution used to render the crops.
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


# ---------------------------------------------------------------------------
# Dispatch async — REMPLACE le pool de threads (_vlm_worker + queue + lock).
# Structure copiée de url_tuning (Semaphore + gather). Piège P5.
# ---------------------------------------------------------------------------
async def _describe_task(
    task: ImageTask,
    semaphore: asyncio.Semaphore,
    vlm: AsyncVlmClient,
) -> tuple[int, VLMResult]:
    """Équivalent async du corps de _vlm_worker : un appel VLM par image,
    résultat indexé par task.index — l'ordre final suit les images."""
    async with semaphore:
        _log.info(
            "[%d/%d] -> VLM — Page %d loc=(%d,%d,%d,%d)",
            task.index, task.total, task.page + 1,
            task.x0, task.y0, task.x1, task.y1,
        )
        description = await describe_image_b64(task.image_b64, task.prompt, vlm)

        if description:
            _log.info("[%d/%d] Description received (%d chars)", task.index, task.total, len(description))
        else:
            _log.warning("[%d/%d] No description returned by the VLM", task.index, task.total)

        return task.index, VLMResult(
            page=task.page,
            x0=task.x0, y0=task.y0,
            x1=task.x1, y1=task.y1,
            description=description,
            raw_tag=task.raw_tag,
        )


async def describe_all_pictures(
    pdf_path: Path,
    pictures: list[PictureTag],
    doc_elements: list[DocElement],
    vlm: AsyncVlmClient,
    language: str = "french",
    n_before: int = N_BEFORE,
    n_after: int = N_AFTER,
    max_concurrency: int = 1,
    dpi: int = DPI_DEFAULT,
    norm: int = NORM,
    preextracted_images_dir: Path | None = None,
) -> dict[int, VLMResult]:
    """
    Crop each image (or load it from preextracted_images_dir), build the
    contextualized prompt, and send it to the VLM.
    Returns a dict indexed 1-based: {index: VLMResult}.

    (Préparation des tâches identique au script — même boucle, mêmes replis ;
    seul le mécanisme d'exécution passe des threads à Semaphore + gather.
    ``max_concurrency`` garde la valeur par défaut de l'ancien --workers : 1.)
    """
    total = len(pictures)
    tasks_data: list[ImageTask] = []

    if preextracted_images_dir:
        _log.info("Queuing %d image(s) — source: %s — %d worker(s)", total, preextracted_images_dir, max_concurrency)
    else:
        _log.info("Queuing %d image(s) — source: fitz crop — %d worker(s)", total, max_concurrency)

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
            tasks_data.append(ImageTask(
                index=i, total=total,
                page=pic["page"],
                x0=pic["x0"], y0=pic["y0"],
                x1=pic["x1"], y1=pic["y1"],
                image_b64=image_b64,
                prompt=prompt,
                raw_tag=pic["raw_tag"],
            ))

    semaphore = asyncio.Semaphore(max_concurrency)
    results_list = await asyncio.gather(
        *(_describe_task(task, semaphore, vlm) for task in tasks_data)
    )
    results: dict[int, VLMResult] = dict(results_list)

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
    :param results: The VLM results indexed by 1-based image index.
    :return: The content with <picture> tags replaced by placeholders.
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
    :param doc_name: Name of the document (used in the Markdown title).
    :param output_path: Path to the Markdown file to write.
    :param vlm_model_name: Name of the VLM model used, shown in the report header.
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


class ImageDescriptionStep(PipelineStep):
    """Décrit les images du doctags via VLM (contexte textuel) → _pictures.doctags
    + _image_descriptions.md."""

    name = "image-description"
    description = "Descriptions d'images via VLM"
    requires_vlm = True

    def __init__(
        self,
        *,
        max_concurrency: int = 1,
        language: str = "french",
        n_before: int = N_BEFORE,
        n_after: int = N_AFTER,
        dpi: int = DPI_DEFAULT,
        norm: int = NORM,
    ) -> None:
        # Mêmes défauts que le parse_args() du script historique.
        self.max_concurrency = max_concurrency
        self.language = language
        self.n_before = n_before
        self.n_after = n_after
        self.dpi = dpi
        self.norm = norm

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.source_pdf, ctx.workspace.reordered_with_tables_doctags]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [
            ctx.workspace.reordered_with_tables_pictures_doctags,
            ctx.workspace.image_descriptions,
        ]

    def execute(self, ctx: PipelineContext) -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # ⚠ PAS asyncio.run() (P7)

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        doctags_path = ws.reordered_with_tables_doctags
        pdf_path = ws.source_pdf
        output_path = ws.reordered_with_tables_pictures_doctags
        markdown_path = ws.image_descriptions
        images_dir = ws.used_images_dir
        doc_name = ws.doc_name

        output_path.parent.mkdir(parents=True, exist_ok=True)

        image_desc_enabled = ctx.settings.enable_image_description
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
            return StepResult(StepStatus.OK, outputs=[output_path], message="no pictures")

        # Step 2 — Export PNGs via fitz (skipped if pre-extracted images are available)
        if images_dir.exists() and any(images_dir.glob("pic*.png")):
            preextracted_dir: Path | None = images_dir
            _log.info("STEP 2 — Pre-extracted images: %s (fitz crop skipped)", preextracted_dir)
        else:
            preextracted_dir = None
            _log.info("STEP 2 — Exporting PNG images (doctags coordinates via fitz)")
            images_dir.mkdir(parents=True, exist_ok=True)
            export_picture_images(pdf_path, pictures, doc_name, images_dir, dpi=self.dpi, norm=self.norm)

        # Step 3 — VLM description (or removal if disabled)
        _log.info("STEP 3 — Describing images with textual context")
        if not image_desc_enabled:
            _log.warning(
                "VLM descriptions disabled for '%s' — <picture> tags removed, "
                "no descriptions file created.",
                doc_name,
            )
            output_path.write_text(remove_picture_tags(content), encoding="utf-8")
            return StepResult(StepStatus.OK, outputs=[output_path], message="descriptions disabled")

        vlm = ctx.vlm()
        if not await vlm.check_connectivity():
            raise StepFailed("VLM unreachable. Check VLM_URL and VLM_CA_PEM.")

        results = await describe_all_pictures(
            pdf_path, pictures, doc_elements, vlm,
            language=self.language,
            max_concurrency=self.max_concurrency,
            n_before=self.n_before,
            n_after=self.n_after,
            dpi=self.dpi,
            norm=self.norm,
            preextracted_images_dir=preextracted_dir,
        )

        # Step 4 — Replacing <picture> tags
        _log.info("STEP 4 — Replacing <picture> tags in the doctags")
        output_path.write_text(replace_picture_tags(content, results), encoding="utf-8")
        _log.info("Enriched doctags saved: %s", output_path)

        # Step 5 — Markdown export
        _log.info("STEP 5 — Exporting descriptions to Markdown")
        export_descriptions_to_markdown(
            results, doc_name, markdown_path, ctx.settings.vlm_model_name
        )
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
