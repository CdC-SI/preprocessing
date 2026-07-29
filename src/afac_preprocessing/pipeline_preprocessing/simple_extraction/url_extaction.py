"""
url_extaction.py — Extraction of hyperlinks (URL, mailto) from a PDF.

Uses PyMuPDF to extract the external links of each page and associates
the text of the words whose center lies within the link's rectangle.
Produces a JSONL file — one line per link found.

Runs independently or after docling_extract.py.

Usage:
    uv run python url_extaction.py --input data/input_files/MonDoc.pdf
    uv run python url_extaction.py --dotenv .env.test
"""
import argparse
import logging
import sys
from pathlib import Path
import fitz  # PyMuPDF
import jsonlines

from ...utils.paths import project_root, resolve_doc_name, resolve_input_pdf

_log = logging.getLogger(__name__)


# Business logic (pure functions)
def is_external_link(uri: str | None) -> bool:
    """
    Return True if the URI is an external link (http, https, mailto).

    :param uri: The URI to check.
    :type uri: str | None
    :return: True if the URI starts with "http://", "https://" or "mailto:".
    :rtype: bool
    """
    return bool(uri and uri.startswith(("http://", "https://", "mailto:")))


def get_link_text(link: dict, words: list[tuple]) -> str:
    """
    Return the text of the words whose center lies within the link's rectangle.

    :param link: The link dict (as returned by PyMuPDF's get_links()), containing a "from" rectangle.
    :type link: dict
    :param words: The list of words on the page, as returned by PyMuPDF's get_text("words").
    :type words: list[tuple]
    :return: The concatenated text of the words inside the link's rectangle, or "No text" if none.
    :rtype: str
    """
    rect = link.get("from")
    if not rect:
        return "No text"
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words
        if rx0 <= (w[0] + w[2]) / 2 <= rx1 and ry0 <= (w[1] + w[3]) / 2 <= ry1
    ]
    return " ".join(link_words).strip() if link_words else "No text"


def serialize_link(link: dict) -> dict:
    """
    Convert fitz.Rect to a list for JSON serialization.

    :param link: The link dict to serialize.
    :type link: dict
    :return: A copy of the link dict with the "from" rectangle converted to a list.
    :rtype: dict
    """
    link_serializable = link.copy()
    if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect):
        link_serializable["from"] = list(link_serializable["from"])
    return link_serializable


def extract_url_links(pdf_path: Path) -> list[dict]:
    """
    Extract all external links from the PDF, page by page.
    Returns a list of dicts with page_number, text, hyperlink, type, details.

    :param pdf_path: Path to the PDF file to process.
    :type pdf_path: Path
    :return: A list of dicts describing each external link found.
    :rtype: list[dict]
    """
    results = []
    with fitz.open(pdf_path) as doc:
        for page_num in range(len(doc)):
            page = doc[page_num]
            links = page.get_links()
            words = page.get_text("words")
            page_links = [
                {
                    "page_number": page_num + 1,
                    "text": get_link_text(link, words),
                    "hyperlink": link.get("uri"),
                    "type": "URI",
                    "details": serialize_link(link),
                }
                for link in links
                if is_external_link(link.get("uri"))
            ]
            if page_links:
                _log.info("  Page %d: %d link(s)", page_num + 1, len(page_links))
            results.extend(page_links)
    return results


def save_links(links: list[dict], output_path: Path) -> None:
    """
    Write the list of links to a JSONL file.

    :param links: The list of link dicts to write.
    :type links: list[dict]
    :param output_path: The path of the output JSONL file.
    :type output_path: Path
    """
    with jsonlines.open(output_path, mode="w") as writer:
        for item in links:
            writer.write(item)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extracts the hyperlinks (http, https, mailto) from a PDF "
            "and saves them to a JSONL file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python url_extaction.py --input data/input_files/MonDoc.pdf\n"
            "  uv run python url_extaction.py "
            "--input data/input_files/MonDoc.pdf "
            "--output data/output_files_preprocessing/MonDoc/hyperlinks_data_MonDoc.jsonl\n"
            "  uv run python url_extaction.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Path to the source PDF. "
            "If absent, resolves data/input_files/<DOC_NAME>.pdf from the environment."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Output JSONL file. "
            "Default: data/output_files_preprocessing/<pdf_name>/hyperlinks_data_<pdf_name>.jsonl"
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help="The .env file to load to resolve DOC_NAME (e.g.: .env.test). Ignored if --input is provided.",
    )
    return parser.parse_args()


# Path resolution
def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Resolve the path of the PDF to process according to the following logic:
    1. If --pdf is provided, use that path.
    2. Otherwise, if --dotenv is provided, load that .env file and read DOC_NAME to build the path data/input_files/<DOC_NAME>.pdf.
    3. Otherwise, read DOC_NAME from the environment and build the path data/input_files/<DOC_NAME>.pdf.
    4. If DOC_NAME is not defined or empty, print an error and exit.

    :param args: The parsed CLI arguments.
    :type args: argparse.Namespace
    :return: The resolved path to the PDF file.
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Resolve the path of the output JSONL file according to the following logic:
    1. If --output is provided, use that path.
    2. Otherwise, build the default path: data/output_files_preprocessing/<pdf_name>/hyperlinks_data_<pdf_name>.jsonl

    :param args: The parsed CLI arguments.
    :type args: argparse.Namespace
    :param pdf_path: The path of the PDF file being processed.
    :type pdf_path: Path
    :return: The resolved path of the output JSONL file.
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"hyperlinks_data_{stem}.jsonl"


# Entry point
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    pdf_path = resolve_pdf(args)
    if not pdf_path.exists():
        raise SystemExit(f"Error: PDF file not found — {pdf_path}")

    output_path = resolve_output(args, pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("Source PDF: %s", pdf_path)
    _log.info("Output    : %s", output_path)

    try:
        links = extract_url_links(pdf_path)
        save_links(links, output_path)
    except Exception:
        _log.exception("Error while extracting links from %s", pdf_path.name)
        sys.exit(1)

    if links:
        _log.info("Done — %d link(s) extracted → %s", len(links), output_path)
    else:
        _log.info("Done — no external link found in %s", pdf_path.name)

    sys.exit(0)


if __name__ == "__main__":
    main()
