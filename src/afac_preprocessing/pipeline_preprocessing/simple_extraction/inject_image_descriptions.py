"""
Step 11 - Inject image descriptions into the final Markdown.
Script: inject_image_descriptions.py

Replaces the [[[IMAGE_DESC:N]]] markers left by description_image_context.py
with the VLM descriptions from the _image_descriptions.md file.

Descriptions are injected AFTER markdown_control_vlm.py (step 10), guaranteeing
they cannot be dropped by earlier VLM steps.

Usage:
    uv run python inject_image_descriptions.py --input data/input_files/MonDoc.pdf
    uv run python inject_image_descriptions.py --dotenv .env.test
    uv run python inject_image_descriptions.py \\
        --markdown data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \\
        --descriptions data/output_files_preprocessing/MonDoc/MonDoc_image_descriptions.md \\
        --output data/output_files_preprocessing/MonDoc/MonDoc_final.md
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from ...utils.paths import project_root, load_env, resolve_doc_name

_log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\[\[\[IMAGE(?:\\)?_DESC:(\d+)\]\]\]")


def parse_image_descriptions_md(descriptions_path: Path) -> dict[int, str]:
    """
    Parse the _image_descriptions.md file and return {index: description}.
    Only entries with an OK status are extracted.

    :param descriptions_path: Path to the _image_descriptions.md file
    :type descriptions_path: Path
    :return: Mapping of image index to its description text
    :rtype: dict[int, str]
    """
    content = descriptions_path.read_text(encoding="utf-8")
    descriptions: dict[int, str] = {}

    for section in re.split(r"\n---\n", content):
        section = section.strip()
        m = re.match(r"## OK - Image (\d+)/\d+[^\n]*\n\n(.*)", section, re.DOTALL)
        if m:
            idx = int(m.group(1))
            desc = m.group(2).strip()
            if desc:
                descriptions[idx] = desc
                _log.debug("Description loaded for IMAGE_DESC:%d (%d chars)", idx, len(desc))

    _log.info("%d description(s) loaded from %s", len(descriptions), descriptions_path.name)
    return descriptions


def inject_descriptions(markdown_content: str, descriptions: dict[int, str]) -> tuple[str, int, int]:
    """
    Replace the [[[IMAGE_DESC:N]]] markers with their matching descriptions.
    Markers inside a list item are inlined (no line breaks).

    :param markdown_content: Markdown content with [[[IMAGE_DESC:N]]] markers
    :type markdown_content: str
    :param descriptions: Mapping of image index to its description text
    :type descriptions: dict[int, str]
    :return: (updated markdown, number injected, number missing)
    :rtype: tuple[str, int, int]
    """
    injected = 0
    missing = 0
    result: list[str] = []

    for line in markdown_content.splitlines(keepends=True):
        m = PLACEHOLDER_RE.search(line)
        if not m:
            result.append(line)
            continue

        idx = int(m.group(1))
        desc = descriptions.get(idx)

        if not desc:
            _log.warning("No description for IMAGE_DESC:%d — marker kept as-is", idx)
            result.append(line)
            missing += 1
            continue

        stripped = line.lstrip()
        is_list_item = bool(re.match(r"[-*+] |\d+\. ", stripped))

        if is_list_item:
            desc_text = desc.replace("\n", " ").strip()
        else:
            desc_text = desc

        new_line = PLACEHOLDER_RE.sub(desc_text, line)
        if not new_line.endswith("\n"):
            new_line += "\n"

        result.append(new_line)
        injected += 1
        _log.info("IMAGE_DESC:%d injected (%d chars)", idx, len(desc))

    return "".join(result), injected, missing


def run(markdown_path: Path, descriptions_path: Path, output_path: Path) -> None:
    """
    Inject the descriptions into the Markdown and save the result.

    :param markdown_path: Markdown file to process
    :type markdown_path: Path
    :param descriptions_path: _image_descriptions.md file produced at step 06
    :type descriptions_path: Path
    :param output_path: Final Markdown output path
    :type output_path: Path
    """
    if not markdown_path.exists():
        raise FileNotFoundError(f"Markdown not found: {markdown_path}")

    _log.info("Source markdown  : %s", markdown_path)
    _log.info("Output           : %s", output_path)

    if descriptions_path.exists():
        _log.info("Descriptions     : %s", descriptions_path)
        descriptions = parse_image_descriptions_md(descriptions_path)
    else:
        # Absent == no images, or descriptions disabled at step 06
        # (description_image_context.py no longer creates this file in that case).
        _log.info("Descriptions     : %s (absent — no images or disabled)", descriptions_path)
        descriptions = {}
    content = markdown_path.read_text(encoding="utf-8")

    found = PLACEHOLDER_RE.findall(content)
    no_placeholders = not found
    no_descriptions = not descriptions

    if no_placeholders and no_descriptions:
        # Normal case: image description was disabled at step 06.
        _log.info("No description and no marker — descriptions disabled. File copied as-is.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    if no_placeholders:
        _log.warning(
            "Descriptions available but no [[[IMAGE_DESC:N]]] marker found in %s. "
            "Check that description_image_context.py is emitting the placeholders.",
            markdown_path.name,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    _log.info("%d marker(s) found: %s", len(found), [int(i) for i in found])

    if no_descriptions:
        _log.warning("Markers present but no description available — file copied without injection.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    updated, injected, missing = inject_descriptions(content, descriptions)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(updated, encoding="utf-8")
    _log.info("Injection done: %d injected, %d missing", injected, missing)
    _log.info("Final markdown saved: %s", output_path)

    if missing:
        _log.warning(
            "%d description(s) missing. "
            "Check _image_descriptions.md or rerun description_image_context.py.",
            missing,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Injects VLM descriptions into the final Markdown, replacing the "
            "[[[IMAGE_DESC:N]]] markers. "
            "Run after markdown_control_vlm.py (step 10)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python inject_image_descriptions.py --input data/input_files/MonDoc.pdf\n"
            "  uv run python inject_image_descriptions.py --dotenv .env.test\n"
            "  uv run python inject_image_descriptions.py \\\n"
            "      --markdown  data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \\\n"
            "      --descriptions data/output_files_preprocessing/MonDoc/MonDoc_image_descriptions.md \\\n"
            "      --output data/output_files_preprocessing/MonDoc/MonDoc_final.md\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help="Path to the source PDF (used to resolve the document stem).",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path,
        default=None,
        help="Markdown to process. Default: data/output_files_preprocessing/<stem>/<stem>_vlm_check.md",
    )
    parser.add_argument(
        "--descriptions", "-d",
        type=Path,
        default=None,
        help="_image_descriptions.md file. Default: data/output_files_preprocessing/<stem>/<stem>_image_descriptions.md",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output markdown. Default: data/output_files_preprocessing/<stem>/<stem>_final.md",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to the .env file to resolve DOC_NAME.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level. Default: INFO.",
    )
    return parser.parse_args()


def _resolve_stem(args: argparse.Namespace) -> str:
    if args.input:
        return args.input.resolve().stem.strip()
    return resolve_doc_name(args, primary_flag="--input")


def _resolve_markdown(args: argparse.Namespace, stem: str) -> Path:
    if args.markdown:
        return args.markdown.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_vlm_check.md"


def _resolve_descriptions(args: argparse.Namespace, stem: str) -> Path:
    if args.descriptions:
        return args.descriptions.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_image_descriptions.md"


def _resolve_output(args: argparse.Namespace, stem: str) -> Path:
    if args.output:
        return args.output.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_final.md"


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dotenv:
        load_env(args.dotenv)

    stem = _resolve_stem(args)
    markdown_path = _resolve_markdown(args, stem)
    descriptions_path = _resolve_descriptions(args, stem)
    output_path = _resolve_output(args, stem)

    try:
        run(markdown_path, descriptions_path, output_path)
    except FileNotFoundError as e:
        _log.exception("%s", e)
        sys.exit(1)
    except Exception:
        _log.exception("Unexpected error during injection.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
