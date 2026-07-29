"""Étape hyq-embedding — embeddings des questions hypothétiques (hyq).

Conversion de ``metadata/hyq_embedding_doc.py`` (vague D). Logique DÉPLACÉE
telle quelle ; l'appel d'embedding passe en async (contrainte C2) via
``embeddings.get_embedding`` — client du ClientBundle, jamais construit ici.

Lit hyq.json (écrit par metadata-generation), génère l'embedding de chaque
question et écrit un CSV dédié par question :
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


def load_hyq(workspace: "DocumentWorkspace") -> list[str]:
    """
    Lit le fichier hyq.json du document.

    :return: Liste de questions hyq
    """
    hyq_path = workspace.hyq_json
    if not hyq_path.exists():
        raise FileNotFoundError(
            f"hyq.json introuvable pour '{workspace.doc_name}' dans {workspace.root.parent}"
        )
    return json.loads(hyq_path.read_text(encoding="utf-8"))


async def write_hyq_csv(
    workspace: "DocumentWorkspace",
    doc_title: str,
    questions: list[str],
    embeddings: "AsyncEmbeddingClient",
) -> tuple[Path, int]:
    """
    Pour chaque question hyq, génère son embedding et écrit un CSV dédié :
    metadata/hyq_<doc_name>/question_1.csv, question_2.csv, …

    Supprime d'abord tout question_*.csv préexistant : sans ça, relancer sur un
    hyq.json régénéré avec moins de questions qu'avant laisse les fichiers en
    trop d'une exécution précédente (ex. question_11.csv orphelin si hyq.json
    est passé de 11 à 10 questions) — silencieusement inclus par tout code qui
    lit hyq_<doc_name>/*.csv comme « l'ensemble courant des questions ».

    Les erreurs par question sont loggées et ignorées — les questions suivantes
    sont toujours traitées. (Boucle séquentielle, comme le script historique.)

    :return: Tuple (chemin du dossier hyq créé, nombre de questions en erreur)
    """
    out_dir = workspace.hyq_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("question_*.csv"):
        stale.unlink()
    n_err = 0

    for i, question in enumerate(questions, start=1):
        _log.info("Embedding question %d/%d : %s...", i, len(questions), question[:60])
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
            _log.exception("Erreur question %d/%d — ignorée.", i, len(questions))
            n_err += 1

    return out_dir, n_err


class HyqEmbeddingStep(PipelineStep):
    """Génère un CSV (CONTENT | METADATA | EMBEDDING) par question hyq."""

    name = "hyq-embedding"
    description = "Embeddings des questions hypothétiques (hyq)"
    requires_vlm = True

    def inputs(self, ctx: "PipelineContext") -> list[Path]:
        return [ctx.workspace.hyq_json]

    def outputs(self, ctx: "PipelineContext") -> list[Path]:
        return [ctx.workspace.hyq_dir]

    def execute(self, ctx: "PipelineContext") -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # ⚠ PAS asyncio.run() (P7)

    async def _execute_async(self, ctx: "PipelineContext") -> StepResult:
        ws = ctx.workspace
        doc_title = f"{ws.doc_name}.pdf"  # même défaut que --doc-title

        _log.info("Chargement des hyq pour : %s", ws.doc_name)
        try:
            questions = load_hyq(ws)
        except FileNotFoundError as exc:
            raise StepFailed(str(exc)) from exc
        _log.info("%d question(s) trouvée(s)", len(questions))

        out_dir, n_err = await write_hyq_csv(ws, doc_title, questions, ctx.embeddings())

        n_ok = len(questions) - n_err
        _log.info("%d/%d fichier(s) CSV écrits dans : %s", n_ok, len(questions), out_dir)
        if n_err:
            _log.warning("%d question(s) en erreur.", n_err)
        return StepResult(
            StepStatus.OK, outputs=self.outputs(ctx), message=f"{n_ok}/{len(questions)} questions"
        )
