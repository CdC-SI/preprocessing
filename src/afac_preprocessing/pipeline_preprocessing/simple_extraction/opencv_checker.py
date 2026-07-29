"""
opencv_checker_modulaire.py — Validation visuelle des doctags Docling sur le PDF source.

Lit un fichier .doctags et superpose les bounding boxes colorées sur chaque page du PDF
rendue en image PNG. Outil de validation uniquement — les images produites ne sont pas
utilisées par les étapes suivantes du pipeline.

Usage :
    uv run python opencv_checker_modulaire.py --input doc.pdf --doctags doc.doctags [options]
    uv run python opencv_checker_modulaire.py --dotenv .env.test
"""
import argparse
import logging
import re
import sys
from pathlib import Path
import cv2
import fitz  # PyMuPDF
import numpy as np

from ...utils.paths import project_root, load_env, resolve_doc_name, resolve_input_pdf

_log = logging.getLogger(__name__)

# DPI 300 : résolution courante pour PDF scan
DPI_DEFAULT = 300

# Docling normalise toutes les coordonnées dans une grille 500×500
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


# Parsing doctags
def parse_doctags_boxes(doctags_path: Path) -> dict[int, list[tuple[int, int, int, int, str]]]:
    """Lit un .doctags et retourne les bounding boxes par page.

    Retourne : { page_num: [(x0, y0, x1, y1, tag), ...] }
    Coordonnées dans la grille normalisée 0–500 de Docling.
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


# Rendu d'une page
def render_page(
    page: fitz.Page,
    boxes: list[tuple[int, int, int, int, str]],
    dpi: int,
) -> np.ndarray:
    """Rend une page PDF et y superpose les bounding boxes colorées."""
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


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validation visuelle des doctags Docling : superpose les bounding boxes "
            "sur chaque page du PDF et exporte les images PNG."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python opencv_checker_modulaire.py "
            "--input data/input_files/MonDoc.pdf --doctags data/output_files_preprocessing/MonDoc/MonDoc.doctags\n"
            "  uv run python opencv_checker_modulaire.py --dotenv .env.test --dpi 150\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Chemin vers le PDF source. "
            "Si absent, résout data/input_files/<DOC_NAME>.pdf depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--doctags", "-d",
        type=Path,
        default=None,
        help=(
            "Chemin vers le fichier .doctags produit par docling_extract.py. "
            "Défaut : data/output_files_preprocessing/<nom_pdf>/<nom_pdf>.doctags."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help=(
            "Dossier de sortie pour les images PNG. "
            "Défaut : data/output_files_preprocessing/<nom_pdf>/opencv_validation/."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DPI_DEFAULT,
        metavar="N",
        help=f"Résolution de rendu PDF en DPI. Défaut : {DPI_DEFAULT}.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --input est fourni.",
    )
    return parser.parse_args()


# Résolution des chemins
def resolve_pdf(args: argparse.Namespace) -> Path:
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_doctags(args: argparse.Namespace, pdf_path: Path) -> Path:
    if args.doctags:
        return args.doctags.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}.doctags"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    if args.output_dir:
        return args.output_dir.resolve()
    return project_root() / "data" / "output_files_preprocessing" / pdf_path.stem.strip() / "opencv_validation"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    pdf_path = resolve_pdf(args)
    doctags_path = resolve_doctags(args, pdf_path)
    output_dir = resolve_output(args, pdf_path)

    if not pdf_path.exists():
        raise SystemExit(f"Erreur : PDF introuvable — {pdf_path}")
    if not doctags_path.exists():
        raise SystemExit(f"Erreur : fichier .doctags introuvable — {doctags_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    _log.info("PDF       : %s", pdf_path)
    _log.info("DocTags   : %s", doctags_path)
    _log.info("Sortie    : %s", output_dir)
    _log.info("DPI       : %d", args.dpi)

    boxes_by_page = parse_doctags_boxes(doctags_path)

    n_pages = 0  # initialisé avant le with pour éviter un NameError si fitz.open échoue
    n_ok = 0
    n_err = 0

    with fitz.open(pdf_path) as doc:
        n_pages = len(doc)
        for page_num in range(n_pages):
            try:
                boxes = boxes_by_page.get(page_num, [])
                img = render_page(doc[page_num], boxes, args.dpi)
                out_path = output_dir / f"page_{page_num + 1}_doctags_boxes.png"
                if not cv2.imwrite(str(out_path), img):
                    raise RuntimeError(f"cv2.imwrite a échoué : {out_path}")
                _log.info("Page %d/%d sauvegardée : %s", page_num + 1, n_pages, out_path)
                n_ok += 1
            except Exception:
                _log.exception("Erreur page %d/%d — ignorée.", page_num + 1, n_pages)
                n_err += 1

    _log.info(
        "Validation terminée — %d/%d page(s) dans : %s",
        n_ok, n_pages, output_dir,
    )
    if n_err:
        _log.warning("%d page(s) en erreur — outil de validation uniquement, pipeline non interrompu.", n_err)
    sys.exit(0)


if __name__ == "__main__":
    main()
