"""hyq-embedding stage, embeddings of hypothetical questions (hyq).

Conversion of metadata/hyq_embedding_doc.py. Logic MOVED as-is,
the embedding call is now asynchronous through
embeddings.get_embedding. ClientBundle client, never constructed here.

Reads hyq.json (written by metadata-generation), generates the embedding for
each question, and writes a dedicated CSV file per question:
metadata/hyq_<doc_name>/question_1.csv, question_2.csv, …
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..clients.openai_client import embedding_to_string
from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..clients.base import AsyncEmbeddingClient
    from ..context import PipelineContext
    from ..workspace import DocumentWorkspace

_log = logging.getLogger(__name__)


def load_hyq(workspace: DocumentWorkspace) -> list[str]:
    """
    Reads the document's hyq.json file.

    :return: List of hyq questions
    """
    hyq_path = workspace.hyq_json
    if not hyq_path.exists():
        raise FileNotFoundError(
            f"hyq.json not found for '{workspace.doc_name}' in {workspace.root.parent}"
        )
    return json.loads(hyq_path.read_text(encoding="utf-8"))


async def write_hyq_csv(
    workspace: DocumentWorkspace,
    doc_title: str,
    questions: list[str],
    embeddings: AsyncEmbeddingClient,
) -> tuple[Path, int]:
    """For each hyq question, generates its embedding and writes a dedicated CSV:
    metadata/hyq_<doc_name>/question_1.csv, question_2.csv, …

    First removes any existing question_*.csv files: without this, rerunning on a
    regenerated hyq.json with fewer questions than before leaves leftover files
    from a previous execution (e.g., orphaned question_11.csv if hyq.json went
    from 11 to 10 questions) — silently included by any code that reads
    hyq_<doc_name>/*.csv as “the current set of questions”.

    Errors for individual questions are logged and ignored — subsequent
    questions are still processed. (Sequential loop, like the historical script.)

    :return: Tuple (path of the created hyq folder, number of failed questions)
    """
    out_dir = workspace.hyq_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("question_*.csv"):
        stale.unlink()
    n_err = 0

    for i, question in enumerate(questions, start=1):
        _log.info("Question embedding %d/%d : %s...", i, len(questions), question[:60])
        try:
            embedding = await embeddings.get_embedding(question)
            csv_path = out_dir / f"question_{i}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
                writer.writerow([
                    question,
                    json.dumps({"title": doc_title}, ensure_ascii=False),
                    embedding_to_string(embedding),
                ])
        except Exception:
            _log.exception("Question %d/%d error ignored.", i, len(questions))
            n_err += 1

    return out_dir, n_err


class HyqEmbeddingStep(PipelineStep):
    """Generates a CSV (CONTENT | METADATA | EMBEDDING) per hyq question."""

    name = "hyq-embedding"
    description = "Hypothetical question embeddings (hyq)"
    requires_vlm = True

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.hyq_json]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.hyq_dir]

    def execute(self, ctx: PipelineContext) -> StepResult:
        return ctx.run_async(self._execute_async(ctx)) 

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        doc_title = f"{ws.doc_name}.pdf" 

        _log.info("Loading hyq for: %s", ws.doc_name)
        try:
            questions = load_hyq(ws)
        except FileNotFoundError as exc:
            raise StepFailed(str(exc)) from exc
        _log.info("%d question(s) found", len(questions))

        out_dir, n_err = await write_hyq_csv(ws, doc_title, questions, ctx.embeddings())

        n_ok = len(questions) - n_err
        _log.info("%d/%d CSV file(s) written to: %s", n_ok, len(questions), out_dir)
        if n_err:
            _log.warning("%d question(s) failed.", n_err)
        return StepResult(
            StepStatus.OK, outputs=self.outputs(ctx), message=f"{n_ok}/{len(questions)} questions"
        )
