"""MetadataEnhancer, VLM enrichment of metadata (summary, intent, hyq).

Collaborator of the metadata-generation stage. 
Conversion of metadata/enhancement_metadata.py
functions and prompts MOVED as-is; calls are now asynchronous
via vlm.text_completion_structured, the client comes from the ClientBundle, never constructed here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..prompts.metadata_prompts import (
    HYQ_PROMPT,
    INTENT_PROMPT_1,
    INTENT_PROMPT_2,
    INTENT_PROMPT_3,
    RESUME_PROMPT,
)

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..workspace import DocumentWorkspace

_log = logging.getLogger(__name__)


# Pydantic models pour le response_format des appels VLM

class ResumeOutput(BaseModel):
    resume: str


class IntentOutput(BaseModel):
    intent: list[str]


class HyQOutput(BaseModel):
    hyq: list[str]


def read_final_markdown(workspace: DocumentWorkspace) -> str:
    """Final markdown of the document (equivalent to _read_markdown: _final.md)."""
    if workspace.final_markdown.exists():
        return workspace.final_markdown.read_text(encoding="utf-8")
    return ""


def write_enrichment_output(
    workspace: DocumentWorkspace,
    resume: str,
    intent: list[str],
    hyq: list[str],
) -> Path:
    """
    Writes the 3 enrichment files into metadata/ (same names and same
    formats as the historical script):
    
    metadata/resume.md - summary in Markdown text format
    metadata/intent.json - list of intents (JSON array)
    metadata/hyq.json - list of hypothetical questions (JSON array)
    """
    out_dir = workspace.metadata_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace.resume_markdown.write_text(resume, encoding="utf-8")
    workspace.intent_json.write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    workspace.hyq_json.write_text(
        json.dumps(hyq, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir


class MetadataEnhancer:
    """The 3 VLM generations of the enrichment — same prompts, asynchronous contract."""

    def __init__(self, vlm: AsyncVlmClient) -> None:
        self._vlm = vlm

    async def generate_resume(self, markdown_content: str) -> str:
        """Generates a short summary of the document markdown via structured output."""
        result = await self._vlm.text_completion_structured(
            RESUME_PROMPT, markdown_content, ResumeOutput
        )
        return result.resume

    async def generate_intent(self, markdown_content: str) -> list[str]:
        """Generates a list of intents/objectives of the document from 3 expert
        perspectives. The 3 calls are merged and deduplicated (same logic as the
        script, sequential, in the same prompt order)."""
        intents: list[str] = []
        seen: set[str] = set()
        for system_prompt in [INTENT_PROMPT_1, INTENT_PROMPT_2, INTENT_PROMPT_3]:
            result = await self._vlm.text_completion_structured(
                system_prompt, markdown_content, IntentOutput
            )
            for item in result.intent:
                normalized = item.strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    intents.append(normalized)
        return intents

    async def generate_hyq(self, markdown_content: str) -> list[str]:
        """Generates a list of hypothetical questions that the document can answer."""
        result = await self._vlm.text_completion_structured(
            HYQ_PROMPT, markdown_content, HyQOutput
        )
        return result.hyq

    async def run(self, workspace: DocumentWorkspace) -> dict:
        """Equivalent of run_enhancement: reads the final markdown, calls the 3 VLM
        generations, and writes resume.md / intent.json / hyq.json."""
        markdown_content = read_final_markdown(workspace)
        if not markdown_content:
            raise FileNotFoundError(
                f"No markdown file found for '{workspace.doc_name}' "
                f"in {workspace.root.parent}"
            )

        _log.info("Summary creation (resume)")
        resume = await self.generate_resume(markdown_content)

        _log.info("Intent creation (intent)")
        intent = await self.generate_intent(markdown_content)

        _log.info("Hypothetical questions creation (hyq)")
        hyq = await self.generate_hyq(markdown_content)

        out_dir = write_enrichment_output(workspace, resume, intent, hyq)
        _log.info("Output written to : %s", out_dir)

        return {"resume": resume, "intent": intent, "hyq": hyq}
