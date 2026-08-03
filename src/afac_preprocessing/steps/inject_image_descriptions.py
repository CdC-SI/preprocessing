"""inject-image-descriptions stage, injection of VLM descriptions into the
final Markdown.

Conversion of the script simple_extraction/inject_image_descriptions.py.
Business functions MOVED as-is.

Replaces the [[[IMAGE_DESC]]] markers left by description_image_context.py
with the VLM descriptions from the _image_descriptions.md file. Descriptions
are injected AFTER markdown_control_vlm.py (step 10), guaranteeing they
cannot be dropped by earlier VLM steps.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\[\[\[IMAGE(?:\\)?_DESC:(\d+)\]\]\]")


# Fonctions métier — déplacées telles quelles
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
            _log.warning("No description for IMAGE_DESC:%d, marker kept as-is", idx)
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


def run_injection(markdown_path: Path, descriptions_path: Path, output_path: Path) -> None:
    """
    Inject the descriptions into the Markdown and save the result.

    (Corps du ``run()`` historique, renommé pour ne pas masquer
    ``PipelineStep.run``, comportement inchangé.)

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
        _log.info("Descriptions     : %s (absent, no images or disabled)", descriptions_path)
        descriptions = {}
    content = markdown_path.read_text(encoding="utf-8")

    found = PLACEHOLDER_RE.findall(content)
    no_placeholders = not found
    no_descriptions = not descriptions

    if no_placeholders and no_descriptions:
        # Normal case: image description was disabled at step 06.
        _log.info("No description and no marker, descriptions disabled. File copied as-is.")
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
        _log.warning("Markers present but no description available, file copied without injection.")
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


class InjectImageDescriptionsStep(PipelineStep):
    """Remplace les marqueurs [[[IMAGE_DESC:N]]] du _vlm_check.md → _final.md."""

    name = "inject-image-descriptions"
    description = "Injection des descriptions d'images → _final.md"
    requires_vlm = False

    def _source_markdown(self, ctx: PipelineContext) -> Path:
        """Markdown à enrichir : la sortie de markdown-control (10) si l'étape
        a tourné, sinon celle de markdown-convert (09).

        markdown-control est sautée par le profil no-vlm, l'injection des
        descriptions ne dépend pas du contrôle VLM.
        """
        ws = ctx.workspace
        for candidate in (ws.vlm_check_markdown, ws.url_vlm_markdown):
            if candidate.exists():
                return candidate
        # None exists: return the nominal value so that validate_input
        # produces the usual error message.
        return ws.vlm_check_markdown

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        # _image_descriptions.md is intentionally absent from the
        # declared inputs: its absence is a nominal case (0 images / disabled)
        return [self._source_markdown(ctx)]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.final_markdown]

    def execute(self, ctx: PipelineContext) -> StepResult:
        try:
            run_injection(
                self._source_markdown(ctx),
                ctx.workspace.image_descriptions,
                ctx.workspace.final_markdown,
            )
        except Exception as exc:
            _log.exception("Unexpected error during injection.")
            raise StepFailed(f"inject-image-descriptions failed: {exc}") from exc
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
