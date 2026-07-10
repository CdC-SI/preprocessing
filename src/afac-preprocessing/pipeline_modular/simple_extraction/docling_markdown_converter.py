"""
docling_markdown_converter.py — Convert enriched doctags to Markdown.

Preprocesses the .doctags file (page splitting, misplaced tag correction),
converts it to Markdown via Docling, then post-processes the custom color and
underline tags.

Usage:
    uv run python stage1_modulaire/docling_markdown_converter.py \
        --input data/output_files_preprocessing/MonDoc/MonDoc.doctags
    uv run python stage1_modulaire/docling_markdown_converter.py --dotenv .env.test
    uv run python stage1_modulaire/docling_markdown_converter.py \
        --input  data/output_files_preprocessing/MonDoc/MonDoc.doctags \
        --output data/output_files_preprocessing/MonDoc/MonDoc.md
"""
import argparse
import logging
import re
import sys
from pathlib import Path

from docling_core.types.doc.document import DocTagsDocument, DoclingDocument

from utils.paths import project_root, load_env, resolve_doc_name

_log = logging.getLogger(__name__)


# Business logic (pure functions)
def _split_pages(content: str) -> str:
    """
    If the content is a single <doctag> block, split it into one block per page using
    </page_footer> (native Docling doctags) or <page_break> (produced by url_tuning_vlm.py)
    as the delimiter — the format from_multipage_doctags_and_images expects.
    Without this split, Docling stops after the first page and ignores the rest.

    :param content: raw doctags content
    :type content: str
    :return: content split into per-page <doctag> blocks
    :rtype: str
    """
    if content.count("<doctag>") > 1:
        return content  # already in the correct multi-page format

    inner = re.sub(r"^\s*</?doctag>\s*", "", content.strip(), flags=re.DOTALL)
    inner = re.sub(r"\s*</doctag>\s*$", "", inner, flags=re.DOTALL)

    # Try </page_footer> first (native Docling doctags),
    # then <page_break> (separator produced by url_tuning_vlm.py and assemble_doctags).
    parts = re.split(r"(?<=</page_footer>)", inner)
    if len(parts) <= 1:
        parts = re.split(r"<page_break\s*/?>", inner)

    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return content  # single-page document, nothing to do

    return "\n".join(f"<doctag>\n{p}\n</doctag>" for p in parts)


def _hoist_misplaced_tags(content: str) -> str:
    """
    Docling cannot handle <section_header_level_N> or <unordered_list> nested inside an
    <ordered_list>. It flattens them into the list, losing headers and section boundaries.
    Extracts these tags from <ordered_list> blocks and places them right after the
    matching </ordered_list>.

    :param content: doctags content
    :type content: str
    :return: content with misplaced tags hoisted out
    :rtype: str
    """
    HOIST = re.compile(
        r"(<section_header_level_\d[^>]*>.*?</section_header_level_\d>|"
        r"<unordered_list>.*?</unordered_list>)",
        re.DOTALL,
    )
    OL = re.compile(r"<ordered_list>(.*?)</ordered_list>", re.DOTALL)

    def _fix_ol(m: re.Match) -> str:
        inner = m.group(1)
        hoisted: list[str] = []

        def _extract(tag_m: re.Match) -> str:
            hoisted.append(tag_m.group(0))
            return ""

        cleaned = HOIST.sub(_extract, inner)
        result = f"<ordered_list>{cleaned}</ordered_list>"
        if hoisted:
            result += "\n" + "\n".join(hoisted)
        return result

    return OL.sub(_fix_ol, content)


def preprocess_doctags(content: str) -> str:
    """
    Preprocess the doctags content: page splitting and misplaced tag correction.
    Public entry point for external modules — avoids coupling on the private helpers.

    :param content: raw doctags content
    :return: preprocessed content, ready for DocTagsDocument
    """
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)
    return content

PAGE_BREAK = "<!-- page-break -->"


def convert_doctags_to_markdown(doctags_path: Path) -> str:
    """
    Read the .doctags file, apply preprocessing, convert to Markdown via Docling
    page by page, then join the pages with a <!-- page-break --> separator.

    :param doctags_path: path to the .doctags file to convert
    :type doctags_path: Path
    :return: final Markdown content with page separators
    :rtype: str
    """
    from utils.markdown_utils import apply_markdown_transforms

    content = doctags_path.read_text(encoding="utf-8")
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)

    page_blocks = re.findall(r"<doctag>(.*?)</doctag>", content, re.DOTALL)

    if not page_blocks:
        doctags_doc = DocTagsDocument.from_multipage_doctags_and_images(content, None)
        doc = DoclingDocument.load_from_doctags(doctags_doc)
        return apply_markdown_transforms(doc.export_to_markdown())

    page_markdowns = []
    for block in page_blocks:
        single = f"<doctag>{block}</doctag>"
        dt = DocTagsDocument.from_multipage_doctags_and_images(single, None)
        doc = DoclingDocument.load_from_doctags(dt)
        md = apply_markdown_transforms(doc.export_to_markdown())
        page_markdowns.append(md.strip())

    return f"\n\n{PAGE_BREAK}\n\n".join(page_markdowns)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Converts an enriched .doctags file to Markdown via Docling. "
            "Preprocesses pages and misplaced tags, "
            "post-processes the custom color and underline tags."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python stage1_modulaire/docling_markdown_converter.py \\\n"
            "      --input data/output_files_preprocessing/MonDoc/MonDoc.doctags\n\n"
            "  # Custom output:\n"
            "  uv run python stage1_modulaire/docling_markdown_converter.py \\\n"
            "      --input  data/output_files_preprocessing/MonDoc/MonDoc.doctags \\\n"
            "      --output data/output_files_preprocessing/MonDoc/MonDoc.md\n\n"
            "  # Via the DOC_NAME environment variable:\n"
            "  uv run python stage1_modulaire/docling_markdown_converter.py \\\n"
            "      --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "The .doctags file to convert. "
            "If absent, resolves data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>.doctags from the environment."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Output Markdown file. "
            "Default: data/output_files_preprocessing/<stem>/<stem>.md"
        ),
    )
    parser.add_argument(
        "--suffix", "-s",
        type=str,
        default="_url_vlm",
        metavar="SUFFIX",
        help=(
            "Suffix to append to the auto-resolved .doctags filename. "
            "Default: _url_vlm → <DOC_NAME>_url_vlm.doctags. "
            "Ignored if --input is given."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to the .env file to resolve DOC_NAME (e.g. .env.test). Ignored if --input is given.",
    )
    return parser.parse_args()


# Path resolution
def resolve_input(args: argparse.Namespace) -> Path:
    """
    Resolve the path of the .doctags file to convert:
    1. --input given → used directly.
    2. Otherwise → read DOC_NAME from the environment (dotenv already loaded in main()).

    :param args: Parsed CLI arguments
    :type args: argparse.Namespace
    :return: Resolved path to the source .doctags file
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    suffix = getattr(args, "suffix", "")
    return project_root() / "data" / "output_files_preprocessing" / doc_name / f"{doc_name}{suffix}.doctags"


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    """
    Resolve the output Markdown file path:
    1. --output given → used directly.
    2. Otherwise → data/output_files_preprocessing/<stem>/<stem>.md

    :param args: Parsed CLI arguments
    :type args: argparse.Namespace
    :param input_path: Path to the source .doctags file
    :type input_path: Path
    :return: Resolved output Markdown path
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    return input_path.parent / f"{input_path.stem}.md"


# Entry point
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    if args.dotenv:
        load_env(args.dotenv)

    input_path = resolve_input(args)
    if not input_path.exists():
        raise SystemExit(f"Error: .doctags file not found — {input_path}")

    output_path = resolve_output(args, input_path)

    _log.info("Input   : %s", input_path)
    _log.info("Output  : %s", output_path)

    try:
        markdown = convert_doctags_to_markdown(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    except Exception:
        _log.exception("Error while converting %s", input_path.name)
        sys.exit(1)

    _log.info("Markdown generated: %s", output_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
