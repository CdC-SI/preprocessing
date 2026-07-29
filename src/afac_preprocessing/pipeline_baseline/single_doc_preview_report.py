"""
single_doc_preview_report.py — Pre-flight comparison report for ONE document, baseline vs.
the preprocessing pipeline, before committing to a full corpus rerun.

IMPORTANT — what this does NOT measure:
  Formal retrieval metrics (Recall@k, Precision@k, nDCG@k, MRR@k, as used in
  retrieval_protocol_evaluation/) require ranking the target document against every OTHER
  document in the corpus. With a single document there is nothing else to rank against —
  sklearn's ndcg_score literally refuses to compute on 1 candidate. Any "retrieval score"
  computed here would be a trivially perfect, meaningless number.

What this DOES measure, as a legitimate but different signal:
  - Structural facts per arm : content length, image/table extraction completeness.
  - Self-similarity : cosine similarity between each arm's own document embedding and each
    HyQ question's embedding (same frozen question set, reused across baseline/pipeline for
    comparability). This says "how semantically close is this representation to questions
    about it" — a proxy for embedding quality, NOT a ranking metric. A higher score here does
    not guarantee better Recall@k once real competing documents are in the corpus.

Usage:
    uv run python single_doc_preview_report.py --doc-name "Adhésion traitement" \\
        --output ../data/baseline_evaluation/single_doc_preview_Adhésion_traitement.md
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

# Bootstrap sys.path from the on-disk layout (not PROJECT_ROOT-aware: this locates the
# *source* directories to import sibling packages from, distinct from utils.paths.project_root()
# below which resolves *data* paths and does honor a PROJECT_ROOT override).
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parent
_RETRIEVAL_EVAL_DIR = _SRC_ROOT / "retrieval_protocol_evaluation"
for _p in (_SRC_ROOT, _RETRIEVAL_EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from utils.paths import project_root  # noqa: E402
from loaders import parse_embedding  # noqa: E402 — même parseur (float32) que le reste du pipeline


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def load_doc_row(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    with open(csv_path, encoding="utf-8") as f:
        return next(csv.DictReader(f), None)


def load_hyq_questions(hyq_dir: Path) -> list[tuple[str, np.ndarray]]:
    """Loads the frozen question set from hyq_<doc>/question_N.csv, in question order."""
    questions = []
    for csv_path in sorted(hyq_dir.glob("question_*.csv"), key=lambda p: int(p.stem.split("_")[1])):
        row = load_doc_row(csv_path)
        if row:
            questions.append((row["CONTENT"], parse_embedding(row["EMBEDDING"])))
    return questions


def count_md_table_rows(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip().startswith("|") and line.strip().endswith("|"))


def count_jsonl_table_rows(content: str) -> int:
    n = 0
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                json.loads(s)
                n += 1
            except json.JSONDecodeError:
                pass
    return n


def build_arm_stats(name: str, content: str, embedding: np.ndarray, questions: list[tuple[str, np.ndarray]]) -> dict:
    sims = [cosine_sim(embedding, q_emb) for _, q_emb in questions]
    return {
        "name": name,
        "content_chars": len(content),
        "md_table_rows": count_md_table_rows(content),
        "jsonl_table_rows": count_jsonl_table_rows(content),
        "self_sim_mean": float(np.mean(sims)) if sims else None,
        "self_sim_min": float(np.min(sims)) if sims else None,
        "self_sim_max": float(np.max(sims)) if sims else None,
        "self_sims": sims,
    }


def render_markdown(doc_name: str, arms: list[dict], questions: list[tuple[str, np.ndarray]]) -> str:
    lines = [
        f"# Pre-flight preview — {doc_name}",
        "",
        "**Scope: 1 document.** Recall@k / Precision@k / nDCG@k / MRR@k are NOT computed here — "
        "they require ranking against the rest of the corpus, which doesn't exist yet in this "
        "preview. The numbers below are structural facts and a self-similarity signal only. "
        "Treat this as a sanity check before the full batch, not as the final comparison.",
        "",
        "## Content structure",
        "",
        "| Arm | Content chars | Markdown table rows | JSON-lines table rows |",
        "|---|---:|---:|---:|",
    ]
    for a in arms:
        lines.append(f"| {a['name']} | {a['content_chars']:,} | {a['md_table_rows']} | {a['jsonl_table_rows']} |")

    lines += [
        "",
        "## Self-similarity — document embedding vs. its own HyQ questions",
        "",
        f"Cosine similarity between each arm's document embedding and each of the {len(questions)} "
        "frozen HyQ question embeddings (same questions reused across all arms for comparability). "
        "**Not a ranking metric** — no other documents are competing here.",
        "",
        "| Arm | Mean | Min | Max |",
        "|---|---:|---:|---:|",
    ]
    for a in arms:
        if a["self_sim_mean"] is None:
            lines.append(f"| {a['name']} | n/a | n/a | n/a |")
        else:
            lines.append(f"| {a['name']} | {a['self_sim_mean']:.4f} | {a['self_sim_min']:.4f} | {a['self_sim_max']:.4f} |")

    lines += ["", "### Per-question similarity", ""]
    header = "| # | Question | " + " | ".join(a["name"] for a in arms) + " |"
    sep = "|---|---|" + "---:|" * len(arms)
    lines += [header, sep]
    for i, (q_text, _) in enumerate(questions):
        q_short = (q_text[:70] + "…") if len(q_text) > 70 else q_text
        row_vals = " | ".join(f"{a['self_sims'][i]:.4f}" for a in arms)
        lines.append(f"| {i + 1} | {q_short} | {row_vals} |")

    lines += [
        "",
        "## Next step",
        "",
        "If these numbers look sane (no arm producing near-0 or NaN similarity, table rows present "
        "where tables were extracted), proceed to the full corpus batch — real Recall@k/nDCG@k "
        "require multiple documents to rank against.",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc-name", required=True)
    parser.add_argument("--stage5", type=Path, default=project_root() / "data" / "output_files_preprocessing")
    parser.add_argument("--baseline-metadata", type=Path, default=project_root() / "data" / "baseline_evaluation" / "baseline_metadata.csv")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    doc_name = args.doc_name

    arms_raw = []

    # baseline: single CSV holding all docs, filter by doc_name
    if args.baseline_metadata.exists():
        with open(args.baseline_metadata, encoding="utf-8") as f:
            row = next((r for r in csv.DictReader(f) if r["doc_name"] == doc_name), None)
        if row:
            arms_raw.append(("baseline", row["CONTENT"], parse_embedding(row["EMBEDDING"])))

    csv_path = args.stage5 / doc_name / "metadata" / f"{doc_name}_final.csv"
    row = load_doc_row(csv_path)
    if row:
        arms_raw.append(("pipeline", row["CONTENT"], parse_embedding(row["EMBEDDING"])))

    if not arms_raw:
        raise SystemExit(f"No data found for {doc_name!r} in any arm.")

    hyq_dir = args.stage5 / doc_name / "metadata" / f"hyq_{doc_name}"
    questions = load_hyq_questions(hyq_dir)
    if not questions:
        raise SystemExit(f"No HyQ questions found in {hyq_dir}")

    arms = [build_arm_stats(name, content, emb, questions) for name, content, emb in arms_raw]
    report = render_markdown(doc_name, arms, questions)

    output_path = args.output or (project_root() / "data" / "baseline_evaluation" / f"single_doc_preview_{doc_name}.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()