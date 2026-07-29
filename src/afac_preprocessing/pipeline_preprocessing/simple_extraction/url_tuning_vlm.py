"""
url_tuning_vlm.py — Integration of hyperlinks into the doctags via VLM.

Uses the VLM to reconstruct the doctags page by page, integrating the
extracted links (URL, mailto) in markdown format [text](url).

Works standalone or at the end of the stage3 pipeline.

Usage:
    uv run python url_tuning_vlm.py --input doc.pdf --doctags doc.doctags --jsonl links.jsonl
    uv run python url_tuning_vlm.py --dotenv .env.test
    uv run python url_tuning_vlm.py --input data/input_files/MonDoc.pdf
"""
import argparse
import asyncio
import base64
import json
import logging
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
from openai import AsyncOpenAI

from ...prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_EN, VLM_PROMPT_CORRECTION_STAGE_3_EN_V3
from ...utils.paths import project_root, load_env, resolve_doc_name, resolve_input_pdf
from ...utils.vlm_client import (
    build_async_client,
    build_vlm_config,
    check_vlm_connectivity_async,
    vision_completion_async,
)

_log = logging.getLogger(__name__)

PROMPT_VARIANTS = {
    "v2": VLM_PROMPT_CORRECTION_STAGE_3_EN,     # JSON lines tables (post load_jsonline_doctags)
    "v3": VLM_PROMPT_CORRECTION_STAGE_3_EN_V3,  # native <otsl> tables, preserved as-is
}


# Business logic (pure functions)
def load_jsonl_links(jsonl_path: Path) -> list[dict]:
    """
    Load the links extracted from the JSONL file into a list of dictionaries.

    :param jsonl_path: Path to the JSONL file containing the extracted links.
    :type jsonl_path: Path
    :return: List of link dictionaries, one per JSONL line.
    :rtype: list[dict]
    """
    links = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                links.append(json.loads(line))
    return links


def get_links_for_page(links: list[dict], page: int) -> list[dict]:
    """
    Filter the list of links to return only those matching the specified page.

    :param links: List of all extracted links.
    :type links: list[dict]
    :param page: Page number to filter on.
    :type page: int
    :return: List of links belonging to the given page.
    :rtype: list[dict]
    """
    return [l for l in links if l.get("page_number") == page]


def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    """
    Open the PDF, render the specified page as an image, and encode that
    image in base64 to send it to the VLM.

    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :param page_num: 1-based page number to render.
    :type page_num: int
    :return: Base64-encoded PNG image of the page.
    :rtype: str
    """
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_num - 1].get_pixmap(dpi=100)
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def build_prompt(page_tags: str, page_links: list[dict], prompt_template: str) -> str:
    """
    Build the prompt to send to the VLM by combining the page's doctags
    with the links extracted from the JSONL file.
    Links are formatted in a human-readable way for the VLM, with their
    associated text and URL, so it can correctly integrate them into the
    doctags content it will reconstruct for the page.

    :param page_tags: Doctags content for the page.
    :type page_tags: str
    :param page_links: Links belonging to this page.
    :type page_links: list[dict]
    :param prompt_template: Prompt template to format (v2 or v3, see --prompt-variant)
    :type prompt_template: str
    :return: Formatted prompt ready to send to the VLM.
    :rtype: str
    """
    # NOTE: the strings below (links_str) are part of the prompt payload sent
    # to the VLM and are intentionally left in French — see task constraint 3.
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" -> url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    if not links_str:
        links_str = "Aucune URL pour cette page."

    return prompt_template.format(
        page_tags=page_tags,
        links_str=links_str,
    )


async def process_page(
    page_num: int,
    page_tags: str,
    page_links: list[dict],
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
    *,
    client: AsyncOpenAI,
    model_name: str,
    prompt_template: str,
) -> tuple[int, str]:
    """
    Process a PDF page by calling the VLM to reconstruct its doctags
    content enriched with the URL links. Retries on transient errors are
    handled by the OpenAI client (max_retries, see
    utils.vlm_client.build_async_client) — no manual retry loop here anymore.

    :param page_num: Page number being processed.
    :type page_num: int
    :param page_tags: Doctags content for the page.
    :type page_tags: str
    :param page_links: Links belonging to this page.
    :type page_links: list[dict]
    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :param semaphore: Semaphore limiting the number of concurrent VLM requests.
    :type semaphore: asyncio.Semaphore
    :param prompt_template: Prompt template to format (v2 or v3, see --prompt-variant)
    :type prompt_template: str
    :return: Tuple of (page number, resulting doctags content for the page).
    :rtype: tuple[int, str]
    """
    async with semaphore:
        _log.info("Page %d: %d link(s) to insert...", page_num, len(page_links))
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = build_prompt(page_tags, page_links, prompt_template)
            result = await vision_completion_async(client, model_name, prompt, image_b64)
            _log.info("Page %d processed.", page_num)
            return page_num, result
        except Exception as e:
            _log.exception("Error on page %d: %s", page_num, e)
            return page_num, page_tags  # fallback: return the page's original doctags


def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    """
    Attempt to split the doctags content into pages using <page_break>
    tags as separators.
    If <page_break> tags are present, the content is split directly on
    those tags, which is more reliable for mapping the right doctags
    portions to each page.
    If no separator tag is found, falls back to an approach that
    distributes the doctags elements approximately based on the total count.

    Re-indexes sequentially over non-empty segments rather than over the
    raw split index: a superfluous or consecutive <page_break> (e.g. an
    artifact from an upstream fix) produces an empty segment which, if
    indexed by raw position, shifts all subsequent page numbers — page N
    then ends up stored under key N+1, and run() never finds it
    (pages_tags.get(N, "") silently empty -> page content lost with no error).

    :param doctags: Full doctags content to split.
    :type doctags: str
    :param n_pages: Expected number of pages (from the PDF).
    :type n_pages: int
    :return: Mapping of page number to its doctags content.
    :rtype: dict[int, str]
    """
    parts = re.split(r'<page_break\s*/?>', doctags)
    non_empty = [content for p in parts if (content := p.strip())]

    if len(non_empty) > 1:
        _log.info("Split by <page_break>: %d page(s) detected.", len(non_empty))
        if len(non_empty) != n_pages:
            _log.warning(
                "%d page(s) detected via <page_break> but %d page(s) expected (PDF) — "
                "check the source doctags for extra or missing <page_break> tags.",
                len(non_empty), n_pages,
            )
        return {i + 1: content for i, content in enumerate(non_empty)}

    _log.warning("No <page_break> found, falling back to distribution by count.")
    pattern = re.compile(
        r'(<(?!/)(?!doctag)\w+>'
        r'(?:<loc_\d+>)*'
        r'.*?'
        r'(?=<(?!loc_)\w+>|</doctag>|$))',
        re.DOTALL
    )
    all_tags = [tag.strip() for tag in pattern.findall(doctags) if tag.strip()]
    tags_per_page = max(1, len(all_tags) // n_pages)

    pages = {}
    for i in range(n_pages):
        start = i * tags_per_page
        end = start + tags_per_page if i < n_pages - 1 else len(all_tags)
        pages[i + 1] = "\n".join(all_tags[start:end])
    return pages


def assemble_doctags(pages: dict[int, str]) -> str:
    """
    Assemble the doctags content of each page into a single overall
    content, making sure to strip internal doctag tags to avoid nesting
    issues, and cleanly joining the contents with line breaks.

    :param pages: Mapping of page number to its doctags content.
    :type pages: dict[int, str]
    :return: Full assembled doctags document, wrapped in <doctag>...</doctag>.
    :rtype: str
    """
    def _strip_doctag_tags(s: str) -> str:
        s = re.sub(r'</?doctags?>', '', s, flags=re.IGNORECASE)
        return s.strip()

    parts = []
    for page_num in sorted(pages):
        content = pages[page_num].strip()
        if not content:
            continue
        content = _strip_doctag_tags(content)
        content = re.sub(r'\n{2,}', '\n', content)
        if content:
            parts.append(content)
    body = "\n<page_break>\n".join(parts).strip()
    return f"<doctag>\n{body}\n</doctag>"


# Main pipeline
async def run(
    pdf_path: Path,
    doctags_path: Path,
    jsonl_path: Path,
    output_path: Path,
    max_workers: int = 1,
    *,
    client: AsyncOpenAI,
    model_name: str,
    prompt_template: str = VLM_PROMPT_CORRECTION_STAGE_3_EN,
) -> None:
    """
    Main entry point of the script: checks VLM connectivity, loads the
    required data, processes each PDF page in parallel, and assembles the
    results into a final doctags file.
    Logs information about the number of pages and links detected, to
    give an idea of the scope of the work to be done.
    Uses a semaphore to limit the number of concurrent requests to the
    VLM, so as not to overload it and to manage resources efficiently.

    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :param doctags_path: Path to the input doctags file.
    :type doctags_path: Path
    :param jsonl_path: Path to the JSONL file containing the extracted links.
    :type jsonl_path: Path
    :param output_path: Path to the output doctags file.
    :type output_path: Path
    :param max_workers: Maximum number of concurrent VLM requests.
    :type max_workers: int
    """
    if not await check_vlm_connectivity_async(client, model_name):
        raise RuntimeError("VLM unreachable, stopping the pipeline.")

    _log.info("=" * 60)
    _log.info("PDF      : %s", pdf_path)
    _log.info("Doctags  : %s", doctags_path)
    _log.info("JSONL    : %s", jsonl_path)
    _log.info("Output   : %s", output_path)
    _log.info("Workers  : %d", max_workers)
    _log.info("=" * 60)

    doctags = doctags_path.read_text(encoding="utf-8")
    links = load_jsonl_links(jsonl_path)

    with fitz.open(str(pdf_path)) as doc:
        n_pages = doc.page_count
    _log.info("%d page(s) detected, %d link(s) total.", n_pages, len(links))

    pages_tags = split_doctags_by_page(doctags, n_pages)

    semaphore = asyncio.Semaphore(max_workers)
    tasks = [
        process_page(
            page_num=p,
            page_tags=pages_tags.get(p, ""),
            page_links=get_links_for_page(links, p),
            pdf_path=pdf_path,
            semaphore=semaphore,
            client=client,
            model_name=model_name,
            prompt_template=prompt_template,
        )
        for p in range(1, n_pages + 1)
        if pages_tags.get(p, "").strip()
    ]

    try:
        results = await asyncio.gather(*tasks)
    finally:
        await client.close()

    processed_pages = dict(sorted(results))
    final_doctags = assemble_doctags(processed_pages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_doctags, encoding="utf-8")
    _log.info("Final doctags saved: %s", output_path)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Integrates hyperlinks into the doctags via VLM, page by page. "
            "Produces a doctags file enriched with URLs in markdown format [text](url)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python url_tuning_vlm.py \\\n"
            "      --input data/input_files/MonDoc.pdf \\\n"
            "      --doctags data/output_files_preprocessing/MonDoc/MonDoc.doctags \\\n"
            "      --jsonl data/output_files_preprocessing/MonDoc/hyperlinks_data_MonDoc.jsonl\n\n"
            "  # Paths automatically resolved from the PDF stem:\n"
            "  uv run python url_tuning_vlm.py --input data/input_files/MonDoc.pdf\n\n"
            "  # Via the DOC_NAME environment variable:\n"
            "  uv run python url_tuning_vlm.py --dotenv .env.test\n"
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
        "--doctags", "-d",
        type=Path,
        default=None,
        help=(
            "Input doctags file to enrich. "
            "Default: data/output_files_preprocessing/<stem>/<stem>_reordered_with_tables_pictures.doctags"
        ),
    )
    parser.add_argument(
        "--jsonl", "-j",
        type=Path,
        default=None,
        help=(
            "JSONL file containing the extracted hyperlinks. "
            "Default: data/output_files_preprocessing/<stem>/hyperlinks_data_<stem>.jsonl"
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Path to the output doctags file. "
            "Default: data/output_files_preprocessing/<stem>/<stem>_url_vlm.doctags"
        ),
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=1,
        metavar="N",
        help="Number of concurrent VLM requests. Default: 1.",
    )
    parser.add_argument(
        "--prompt-variant",
        choices=sorted(PROMPT_VARIANTS),
        default="v2",
        help=(
            "v2: JSON lines tables (pipeline with load_jsonline_doctags.py). "
            "v3: native Docling <otsl> tables, preserved as-is (pipeline without JSON conversion). "
            "Default: v2."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help="Env file to load to resolve DOC_NAME (e.g.: .env.test). Ignored if --input is provided.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level. Default: INFO.",
    )
    return parser.parse_args()


# Path resolution
def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Resolve the PDF path according to the following logic:
    1. --input provided -> used directly.
    2. Otherwise -> reads DOC_NAME from the environment (the dotenv file
       is already loaded in main()).

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :return: Resolved path to the source PDF.
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_doctags(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Resolve the path to the input doctags file.
    If --doctags is provided, uses it directly.
    Otherwise, builds the default path: data/output_files_preprocessing/<stem>/<stem>.doctags

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :return: Resolved path to the input doctags file.
    :rtype: Path
    """
    if args.doctags:
        return args.doctags.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_reordered_with_tables_pictures.doctags"


def resolve_jsonl(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Resolve the path to the JSONL file containing the links.
    If --jsonl is provided, uses it directly.
    Otherwise, builds the default path: data/output_files_preprocessing/<stem>/hyperlinks_data_<stem>.jsonl

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :return: Resolved path to the JSONL links file.
    :rtype: Path
    """
    if args.jsonl:
        return args.jsonl.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"hyperlinks_data_{stem}.jsonl"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Resolve the path to the output doctags file.
    If --output is provided, uses it directly.
    Otherwise, builds the default path: data/output_files_preprocessing/<stem>/<stem>_url_vlm.doctags

    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    :param pdf_path: Path to the source PDF file.
    :type pdf_path: Path
    :return: Resolved path to the output doctags file.
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_url_vlm.doctags"


# Entry point
def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    vlm_cfg = build_vlm_config(dotenv_path=args.dotenv)
    client = build_async_client(vlm_cfg)

    pdf_path = resolve_pdf(args)
    if not pdf_path.exists():
        raise SystemExit(f"Error: PDF file not found — {pdf_path}")

    doctags_path = resolve_doctags(args, pdf_path)
    if not doctags_path.exists():
        raise SystemExit(
            f"Error: doctags file not found — {doctags_path}\n"
            "Hint: use --doctags <path> to specify the input file."
        )

    jsonl_path = resolve_jsonl(args, pdf_path)
    if not jsonl_path.exists():
        raise SystemExit(
            f"Error: JSONL file not found — {jsonl_path}\n"
            "Hint: use --jsonl <path> to specify the links file."
        )

    output_path = resolve_output(args, pdf_path)

    try:
        asyncio.run(run(
            pdf_path=pdf_path,
            doctags_path=doctags_path,
            jsonl_path=jsonl_path,
            output_path=output_path,
            max_workers=args.workers,
            client=client,
            model_name=vlm_cfg.vlm_model_name,
            prompt_template=PROMPT_VARIANTS[args.prompt_variant],
        ))
    except RuntimeError as e:
        _log.exception("%s", e)
        sys.exit(1)
    except Exception:
        _log.exception("Unexpected error during processing.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
