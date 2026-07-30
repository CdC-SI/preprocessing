"""
"Raw docling" retrieval baseline for the Adhésion subset (20 documents).

Goal: measure the contribution of the preprocessing pipeline by comparing two
document representations evaluated with exactly the same protocol (same HyQ
questions, same corpus, same metrics):
  - current pipeline: embedding of "<doc>_final.md" (enriched/preprocessed content)
  - baseline        : embedding of "<doc>.md" (raw docling output, no preprocessing)

Only the document representation changes. The HyQ questions (text + embedding)
are reused as-is from metadata/hyq_<doc>/question_N.csv: their embedding does
not depend on the preprocessing pipeline (same model, same text), so
recomputing them would waste API calls and add no extra information. Do not
concatenate HyQ + content into a single vector: the reference protocol
(retrieval_protocol_evaluation) always compares a question embedding to a
document embedding via cosine similarity — merging them would produce a
metric that isn't comparable to the pipeline.

The metrics/aggregation comparison reuses evaluate_doc() from
retrieval_protocol_evaluation/evaluate_all_docs.py (semantic pipeline, no
reranker) to guarantee a Recall/Precision/nDCG/MRR@k computation strictly
identical to the reference pipeline's.

Inputs:
  data/output_files_preprocessing/<doc>/<doc>.md                          — raw docling markdown (20 docs)
  data/output_files_preprocessing/<doc>/metadata/hyq.json                 — HyQ questions (text)
  data/output_files_preprocessing/<doc>/metadata/hyq_<doc>/question_N.csv — HyQ embeddings (reused as-is)

Outputs (--output-dir, default data/baseline_evaluation/):
  baseline_metadata.csv — one row per document: CONTENT (raw docling md),
                          METADATA (minimal dict, without the pipeline's
                          enriched fields), HYQ (associated questions, for
                          traceability), EMBEDDING (new embedding computed
                          on CONTENT alone)
  baseline_results.csv  — one row per (document, HyQ question): same columns
                          as evaluation_results.csv (recall/precision/ndcg/mrr@k),
                          computed with the baseline embeddings. Directly
                          comparable to data/pipeline_evaluation/<doc>/evaluation_results.csv.

Document outputs (--docs-output-dir, default data/output_files_baseline/):
  <doc>/<doc>.md — copy of the raw docling markdown actually used for the
                   baseline embedding, for side-by-side visual inspection
                   with data/output_files_preprocessing/<doc>/.

Usage:
uv run python single_docling_baseline.py
uv run python single_docling_baseline.py --dotenv ../.env.test --top-ks 1,3,5,10,20
"""
import argparse
import asyncio
import csv
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from openai import AsyncOpenAI

from ..clients.openai_client import (
    build_async_embedding_client,
    embedding_to_string,
    get_embedding_async,
)
from ..retrieval_protocol_evaluation.config import TOP_KS
from ..retrieval_protocol_evaluation.evaluate_all_docs import evaluate_doc
from ..retrieval_protocol_evaluation.loaders import parse_embedding as parse_embedding_field
from ..retrieval_protocol_evaluation.loaders import resolve_doc_dir
from ..settings import Settings, _find_project_root, default_dotenv

# ``generate_embedding`` (metadata/embedding_metadata.py) n'était qu'un alias de
# ``get_embedding`` ; le module a disparu au lot 6 (→ steps/document_embedder.py).
project_root = _find_project_root

_log = logging.getLogger(__name__)

DEFAULT_STAGE5 = project_root() / "data" / "output_files_preprocessing"
DEFAULT_OUTPUT_DIR = project_root() / "data" / "baseline_evaluation"
DEFAULT_DOCS_OUTPUT_DIR = project_root() / "data" / "output_files_baseline"


# Discovery
def discover_doc_names(stage5_dir: Path) -> list[str]:
    """Doc names with a raw docling markdown, a document embedding and at least one HyQ question."""
    names = []
    for csv_path in sorted(stage5_dir.rglob("metadata/*_final.csv")):
        doc_name = csv_path.stem.removesuffix("_final")
        hyq_dir = csv_path.parent / f"hyq_{doc_name}"
        raw_md = csv_path.parent.parent / f"{doc_name}.md"
        if raw_md.exists() and hyq_dir.exists() and any(hyq_dir.glob("question_*.csv")):
            names.append(doc_name)
        else:
            _log.warning("Skipping '%s': missing raw markdown or HyQ questions.", doc_name)
    return names


def build_baseline_embedding_client(dotenv_path: Path | None) -> tuple[AsyncOpenAI, str]:
    """Client + model name for the baseline's own embedding calls — same helpers the rest
    of the pipeline uses (clients/openai_client.py), so retry/timeout/CA handling stays
    identical."""
    settings = Settings.from_dotenv(dotenv_path)
    return build_async_embedding_client(settings), settings.embedding_model_name


def copy_raw_markdown(doc_name: str, stage5_dir: Path, docs_output_dir: Path) -> None:
    """Copy <stage5_dir>/<doc>/<doc>.md (raw docling, source of the baseline embedding) into
    <docs_output_dir>/<doc>/<doc>.md, so it can be browsed the same way as output_files_preprocessing."""
    src = resolve_doc_dir(stage5_dir, doc_name) / f"{doc_name}.md"
    dst = docs_output_dir / doc_name / f"{doc_name}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


# baseline_metadata.csv
async def build_baseline_metadata(
    doc_names: list[str],
    stage5_dir: Path,
    client: AsyncOpenAI,
    embedding_model_name: str,
    docs_output_dir: Path,
) -> list[dict]:
    """One row per doc: raw docling CONTENT, minimal METADATA, associated HYQ (traceability only), new EMBEDDING."""
    rows = []
    for doc_name in doc_names:
        doc_dir = resolve_doc_dir(stage5_dir, doc_name)
        content = (doc_dir / f"{doc_name}.md").read_text(encoding="utf-8")
        copy_raw_markdown(doc_name, stage5_dir, docs_output_dir)

        hyq_path = doc_dir / "metadata" / "hyq.json"
        questions = json.loads(hyq_path.read_text(encoding="utf-8")) if hyq_path.exists() else []

        metadata = {
            "title": f"{doc_name}.pdf",
            "source": "afac",
            "doctype": "pdf",
            "language": "fr",
            "embedding_model": embedding_model_name,
        }

        _log.info("Embedding baseline '%s' (%d chars)", doc_name, len(content))
        embedding = await get_embedding_async(client, embedding_model_name, content)

        rows.append({
            "doc_name": doc_name,
            "CONTENT": content,
            "METADATA": json.dumps(metadata, ensure_ascii=False),
            "HYQ": json.dumps(questions, ensure_ascii=False),
            "EMBEDDING": embedding_to_string(embedding),
        })
    return rows


def save_baseline_metadata(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["doc_name", "CONTENT", "METADATA", "HYQ", "EMBEDDING"])
        for row in rows:
            writer.writerow([row["doc_name"], row["CONTENT"], row["METADATA"], row["HYQ"], row["EMBEDDING"]])
    _log.info("Baseline metadata saved → %s", output_path)


# Evaluation — reuses evaluate_doc() (semantic pipeline) from evaluate_all_docs.py
async def evaluate_baseline(
    doc_names: list[str],
    doc_embeddings: np.ndarray,
    stage5_dir: Path,
    top_ks: list[int],
) -> list[dict]:
    rows: list[dict] = []
    for doc_name in doc_names:
        _log.info("── %s", doc_name)
        sem_rows, _ = await evaluate_doc(
            doc_name=doc_name,
            doc_names=doc_names,
            doc_embeddings=doc_embeddings,
            doc_texts=[""] * len(doc_names),  # unused: reranker disabled
            stage5_dir=stage5_dir,
            top_ks=top_ks,
            use_reranker=False,
        )
        rows.extend(sem_rows)
    return rows


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrieval baseline on the raw docling markdown (no preprocessing).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--docs-output-dir", type=Path, default=DEFAULT_DOCS_OUTPUT_DIR)
    # Défaut = default_dotenv() : .env puis .env.test, cwd puis racine du
    # dépôt — même résolution que la CLI afac-preprocess.
    parser.add_argument("--dotenv", type=Path, default=default_dotenv())
    parser.add_argument(
        "--top-ks",
        default=",".join(map(str, TOP_KS)),
        help=f"Comma-separated k values. Default: {','.join(map(str, TOP_KS))}",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    top_ks = [int(k) for k in args.top_ks.split(",")]

    doc_names = discover_doc_names(args.stage5)
    if not doc_names:
        _log.error("No document found in %s", args.stage5)
        sys.exit(1)
    _log.info("Baseline subset: %d document(s)", len(doc_names))

    client, embedding_model_name = build_baseline_embedding_client(args.dotenv)

    metadata_rows = await build_baseline_metadata(
        doc_names, args.stage5, client, embedding_model_name, args.docs_output_dir
    )
    save_baseline_metadata(metadata_rows, args.output_dir / "baseline_metadata.csv")

    doc_embeddings = np.stack([parse_embedding_field(row["EMBEDDING"]) for row in metadata_rows])

    results_rows = await evaluate_baseline(doc_names, doc_embeddings, args.stage5, top_ks)
    if not results_rows:
        _log.error("No evaluation result generated.")
        sys.exit(1)

    results_path = args.output_dir / "baseline_results.csv"
    pd.DataFrame(results_rows).to_csv(results_path, index=False)
    _log.info("Baseline results saved → %s", results_path)

    _log.info("Done. %d document(s), %d question(s) evaluated.", len(doc_names), len(results_rows))


if __name__ == "__main__":
    asyncio.run(main())
