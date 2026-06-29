"""Load HyQ question CSVs and document embedding CSVs from stage5 output."""
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import DOC_FOLDER_SUFFIX, HYQ_FOLDER_PREFIX, DOC_CSV_SUFFIX

_log = logging.getLogger(__name__)


@dataclass
class QuestionRecord:
    question_idx: int
    content: str
    source_doc_title: str
    embedding: np.ndarray


@dataclass
class DocRecord:
    doc_name: str
    embedding: np.ndarray


def parse_embedding(embedding_str: str) -> np.ndarray:
    return np.array([float(v.strip()) for v in embedding_str.split(",")], dtype=np.float32)


def _read_single_csv_row(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def load_hyq_questions(stage5_dir: Path, doc_name: str) -> list[QuestionRecord]:
    """Load HyQ questions from hyq.json (text) and question_N.csv files (embeddings only)."""
    metadata_dir = stage5_dir / f"{doc_name}{DOC_FOLDER_SUFFIX}" / "metadata"
    hyq_path = metadata_dir / "hyq.json"

    if not hyq_path.exists():
        raise FileNotFoundError(f"hyq.json not found: {hyq_path}")

    question_texts: list[str] = json.loads(hyq_path.read_text(encoding="utf-8"))

    hyq_dir = metadata_dir / f"{HYQ_FOLDER_PREFIX}{doc_name}"
    if not hyq_dir.exists():
        raise FileNotFoundError(f"HyQ directory not found: {hyq_dir}")

    records: list[QuestionRecord] = []
    for idx, text in enumerate(question_texts, start=1):
        csv_path = hyq_dir / f"question_{idx}.csv"
        if not csv_path.exists():
            _log.warning("Missing embedding CSV for question %d of '%s', skipping.", idx, doc_name)
            continue
        row = _read_single_csv_row(csv_path)
        records.append(QuestionRecord(
            question_idx=idx,
            content=text,
            source_doc_title=f"{doc_name}.pdf",
            embedding=parse_embedding(row["EMBEDDING"]),
        ))

    _log.info("Loaded %d HyQ question(s) for '%s'", len(records), doc_name)
    return records


def load_all_doc_embeddings(stage5_dir: Path) -> list[DocRecord]:
    """Load all *_final.csv document embedding files found under stage5_dir."""
    records: list[DocRecord] = []
    pattern = f"*{DOC_FOLDER_SUFFIX}/metadata/*{DOC_CSV_SUFFIX}"
    for csv_path in sorted(stage5_dir.glob(pattern)):
        doc_name = csv_path.stem.removesuffix("_final")
        try:
            row = _read_single_csv_row(csv_path)
            records.append(DocRecord(
                doc_name=doc_name,
                embedding=parse_embedding(row["EMBEDDING"]),
            ))
        except (StopIteration, KeyError):
            _log.warning("Skipping malformed CSV: %s", csv_path)

    _log.info("Loaded %d document embedding(s) from %s", len(records), stage5_dir)
    return records


def load_doc_resumes(stage5_dir: Path) -> dict[str, str]:
    """Return {doc_name: resume_text} for all docs that have a resume.md."""
    resumes: dict[str, str] = {}
    for resume_path in sorted(stage5_dir.glob("*/metadata/resume.md")):
        doc_name = resume_path.parent.parent.name
        resumes[doc_name] = resume_path.read_text(encoding="utf-8").strip()
    _log.info("Loaded %d document resume(s) from %s", len(resumes), stage5_dir)
    return resumes
