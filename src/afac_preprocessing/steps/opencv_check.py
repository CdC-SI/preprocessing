"""opencv-check stage, visual validation of doctags on the source PDF.

Conversion of the simple_extraction/opencv_checker.py script.
Business functions MOVED as-is.

Overlays colored doctags bounding boxes on each rendered PDF page as PNG.
Validation tool only, no output is consumed by subsequent stages; disabled by
default (enabled_by_default=False).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import fitz
import numpy as np

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)

DPI_DEFAULT = 300

# Docling normalizes all coordinates on a 500×500 grid
# https://github.com/docling-project/docling/discussions/354
NORM_MAX = 500

COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "picture":                 (0,   0,   255),
    "text":                    (0,   255, 0),
    "table":                   (255, 0,   0),
    "page_header":             (255, 255, 0),
    "page_footer":             (255, 0,   255),
    "section_header_level_1":  (0,   128, 255),
    "section_header_level_2":  (128, 128, 0),
    "section_header_level_3":  (0,   128, 128),
    "otsl":                    (128, 0,   255),
    "unordered_list":          (0,   0,   0),
    "ordered_list":            (128, 128, 255),
    "list_item":               (255, 128, 0),
    "table_cell":              (128, 0,   0),
    "table_row":               (0,   128, 0),
    "table_header":            (0,   0,   128),
    "caption":                 (255, 192, 203),
    "footnote":                (128, 128, 128),
    "reference":               (255, 215, 0),
    "figure":                  (255, 140, 0),
    "equation":                (0,   255, 127),
    "highlight":               (255, 255, 255),
    "link":                    (0,   0,   0),
    "fcel":                    (128, 0,   255),
    "ched":                    (0,   200, 200),
    "ecel":                    (200, 200, 0),
    "lcel":                    (200, 0,   200),
    "rhed":                    (0,   100, 255),
    "nl":                      (150, 150, 150),
}


# Doctags parsing, moved as-is
def parse_doctags_boxes(doctags_path: Path) -> dict[int, list[tuple[int, int, int, int, str]]]:
    """Reads a .doctags file and returns the bounding boxes per page.
    
    Returns: { page_num: [(x0, y0, x1, y1, tag), ...] }
    Coordinates are in Docling's normalized 0–500 grid.
    """
    boxes_by_page: dict[int, list[tuple[int, int, int, int, str]]] = {}
    current_page = 0

    with doctags_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.replace("<doctag>", "").replace("</doctag>", "").strip()
            if not line:
                continue

            for tag_match in re.finditer(r"<(?!/)(\w+)>", line):
                tag = tag_match.group(1)
                rest = line[tag_match.end():]
                coords = re.match(
                    r"<loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)>", rest
                )
                if coords:
                    x0, y0, x1, y1 = map(int, coords.groups())
                    boxes_by_page.setdefault(current_page, []).append((x0, y0, x1, y1, tag))

            if "<page_footer>" in line:
                current_page += 1

    return boxes_by_page


# Page rendering, moved as-is
def render_page(
    page: fitz.Page,
    boxes: list[tuple[int, int, int, int, str]],
    dpi: int,
) -> np.ndarray:
    """Renders a PDF page and overlays the colored bounding boxes on it."""
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    img = img.copy()
    img_h, img_w = img.shape[:2]

    for x0n, y0n, x1n, y1n, tag in boxes:
        x0 = int(x0n / NORM_MAX * img_w)
        y0 = int(y0n / NORM_MAX * img_h)
        x1 = int(x1n / NORM_MAX * img_w)
        y1 = int(y1n / NORM_MAX * img_h)
        color = COLOR_MAP.get(tag, (0, 255, 255))
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        cv2.putText(img, tag, (x0, max(y0 - 5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    return img


class OpencvCheckStep(PipelineStep):
    """Visual QA: PNG per page with doctags bounding boxes"""

    name = "opencv-check"
    description = "OpenCV visual QA (no output consumed downstream)"
    requires_vlm = False
    enabled_by_default = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.source_pdf, ctx.workspace.doctags]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.opencv_validation_dir]

    def execute(self, ctx: PipelineContext) -> StepResult:
        pdf_path = ctx.workspace.source_pdf
        doctags_path = ctx.workspace.doctags
        output_dir = ctx.workspace.opencv_validation_dir
        dpi = DPI_DEFAULT

        output_dir.mkdir(parents=True, exist_ok=True)

        _log.info("PDF       : %s", pdf_path)
        _log.info("DocTags   : %s", doctags_path)
        _log.info("Output    : %s", output_dir)
        _log.info("DPI       : %d", dpi)

        try:
            boxes_by_page = parse_doctags_boxes(doctags_path)

            n_pages = 0
            n_ok = 0
            n_err = 0

            with fitz.open(pdf_path) as doc:
                n_pages = len(doc)
                for page_num in range(n_pages):
                    try:
                        boxes = boxes_by_page.get(page_num, [])
                        img = render_page(doc[page_num], boxes, dpi)
                        out_path = output_dir / f"page_{page_num + 1}_doctags_boxes.png"
                        if not cv2.imwrite(str(out_path), img):
                            raise RuntimeError(f"cv2.imwrite failed : {out_path}")
                        _log.info("Page %d/%d saved: %s", page_num + 1, n_pages, out_path)
                        n_ok += 1
                    except Exception:
                        _log.exception("Error on page %d/%d,  skipped.", page_num + 1, n_pages)
                        n_err += 1
        except Exception as exc:
            raise StepFailed(f"opencv-check failed on {pdf_path.name}: {exc}") from exc

        _log.info("Validation completed, %d/%d page(s) in: %s", n_ok, n_pages, output_dir)
        if n_err:
            _log.warning(
                "%d page(s) with errors, validation tool only, pipeline not interrupted.",
                n_err,
            )
        # Historical behavior: always exit 0, even with pages containing errors.
        return StepResult(
            StepStatus.OK, outputs=self.outputs(ctx), message=f"{n_ok}/{n_pages} page(s)"
        )
