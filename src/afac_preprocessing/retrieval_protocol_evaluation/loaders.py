"""Load HyQ question CSVs and document embedding CSVs from stage5 output.

Two families of question loaders live here:

* ``load_hyq_questions`` / ``load_all_pipeline_questions`` — the questions the
  pipeline generated itself (``metadata/hyq.json`` + pre-computed embeddings).
* ``parse_questions_file`` / ``load_custom_questions`` — an externally authored
  question set (the slot-based variants), embedded on the fly.

Both produce ``QuestionRecord``, so everything downstream (similarity, metrics,
report) is indifferent to where the questions came from — which is what makes an
arm-vs-arm comparison possible at all.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .config import DOC_CSV_SUFFIX, DOC_FOLDER_SUFFIX, HYQ_FOLDER_PREFIX

if TYPE_CHECKING:
    from ..clients.base import AsyncEmbeddingClient

_log = logging.getLogger(__name__)

# Keys of a custom question record (see parse_questions_file).
QUESTION_KEY = "question"
SOURCE_KEY = "source_doc"
SLOTS_KEY = "slots"
ID_KEY = "id"


@dataclass
class QuestionRecord:
    question_idx: int
    content: str
    source_doc_title: str
    embedding: np.ndarray
    # Both default: the pipeline loaders below construct positionally with the
    # four historical fields and must keep working untouched.
    slots: dict[str, str] = field(default_factory=dict)
    question_id: str = ""


@dataclass
class DocRecord:
    doc_name: str
    embedding: np.ndarray


def parse_embedding(embedding_str: str) -> np.ndarray:
    return np.array([float(v.strip()) for v in embedding_str.split(",")], dtype=np.float32)


def _read_single_csv_row(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def resolve_doc_dir(stage5_dir: Path, doc_name: str) -> Path:
    """Dossier de sortie d'un document, quel que soit le layout.

    ⚠ Lot 9 : ces loaders supposaient ``stage5_dir / doc_name`` — le layout
    PLAT d'avant F1. Depuis, la sortie reproduit l'arborescence d'entrée
    (``<source>/<thème>/<doc>/``) et cette hypothèse ne trouvait plus rien :
    l'évaluation et la baseline retournaient silencieusement 0 document.

    On cherche d'abord à plat (rétrocompatible avec les sorties pré-F1), puis
    récursivement. Le dossier retenu doit porter un ``metadata/``, pour ne pas
    confondre avec un dossier de thème homonyme.
    """
    flat = stage5_dir / f"{doc_name}{DOC_FOLDER_SUFFIX}"
    if (flat / "metadata").is_dir():
        return flat
    for candidate in sorted(stage5_dir.rglob(f"{doc_name}{DOC_FOLDER_SUFFIX}")):
        if candidate.is_dir() and (candidate / "metadata").is_dir():
            return candidate
    return flat  # laisse l'appelant lever son FileNotFoundError habituel


def load_hyq_questions(stage5_dir: Path, doc_name: str) -> list[QuestionRecord]:
    """Load HyQ questions from hyq.json (text) and question_N.csv files (embeddings only)."""
    metadata_dir = resolve_doc_dir(stage5_dir, doc_name) / "metadata"
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


def discover_doc_names(stage5_dir: Path) -> list[str]:
    """All doc names that have both a document embedding and at least one HyQ question."""
    names = []
    for csv_path in sorted(stage5_dir.rglob(f"metadata/*{DOC_CSV_SUFFIX}")):
        doc_name = csv_path.stem.removesuffix("_final")
        hyq_dir = csv_path.parent / f"{HYQ_FOLDER_PREFIX}{doc_name}"
        if hyq_dir.exists() and any(hyq_dir.glob("question_*.csv")):
            names.append(doc_name)
    return names


def load_all_pipeline_questions(stage5_dir: Path) -> list[QuestionRecord]:
    """Every pipeline-generated HyQ across the corpus, as one flat set.

    This is the control arm of a question-set comparison: the same shape as a
    custom question file, so both go through an identical evaluation path.

    ``question_idx`` is renumbered over the flat set (the per-document index
    survives in ``question_id`` as ``<doc>#<n>``) — the per-question heatmap
    indexes rows by ``question_idx`` and would otherwise collide across docs.
    """
    records: list[QuestionRecord] = []
    for doc_name in discover_doc_names(stage5_dir):
        try:
            doc_records = load_hyq_questions(stage5_dir, doc_name)
        except FileNotFoundError as exc:
            _log.warning("Skipping '%s': %s", doc_name, exc)
            continue
        for record in doc_records:
            record.question_id = f"{doc_name}#{record.question_idx}"
            records.append(record)

    for idx, record in enumerate(records, start=1):
        record.question_idx = idx

    _log.info("Loaded %d pipeline HyQ question(s) from %s", len(records), stage5_dir)
    return records


def load_all_doc_embeddings(stage5_dir: Path) -> list[DocRecord]:
    """Load all *_final.csv document embedding files found under stage5_dir."""
    records: list[DocRecord] = []
    # rglob (et non glob) depuis F1 : les documents vivent sous
    # <source>/<thème>/<doc>/metadata/, plus directement sous stage5_dir.
    for csv_path in sorted(stage5_dir.rglob(f"metadata/*{DOC_CSV_SUFFIX}")):
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
    for resume_path in sorted(stage5_dir.rglob("metadata/resume.md")):
        doc_name = resume_path.parent.parent.name
        resumes[doc_name] = resume_path.read_text(encoding="utf-8").strip()
    _log.info("Loaded %d document resume(s) from %s", len(resumes), stage5_dir)
    return resumes


# --- Externally authored question sets (slot-based variants) ---------------


def parse_questions_file(path: Path) -> tuple[str, list[dict]]:
    """Read a question file -> (variant label, validated raw records).

    Accepts the full envelope::

        {"variant": "slots_v1", "questions": [{...}, ...]}

    or a bare list of records. Every record needs ``question`` and
    ``source_doc``; ``slots`` and ``id`` are optional.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        variant = str(raw.get("variant") or path.stem)
        entries = raw.get("questions", [])
    else:
        variant, entries = path.stem, raw

    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: no question found.")

    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry #{position} is not an object.")
        for key in (QUESTION_KEY, SOURCE_KEY):
            if not entry.get(key):
                raise ValueError(f"{path}: entry #{position} is missing '{key}'.")

    return variant, entries


def validate_ground_truth(
    entries: list[dict], corpus_doc_names: list[str], *, strict: bool = True
) -> list[dict]:
    """Every ``source_doc`` must name a document actually present in the corpus.

    An unmatched ``source_doc`` is NOT harmless. ``metrics._y_vectors`` builds an
    all-zero ``y_true``, and ``mrr_at_k`` then reads ``ranks[np.argmax(y_true)]``
    — ``argmax`` of an all-zero vector is 0, so the question gets silently scored
    against whichever document happens to sit first in the corpus. The run
    produces plausible, wrong numbers instead of failing, which is the worst
    outcome for a comparison whose whole point is trusting the deltas.
    """
    known = set(corpus_doc_names)
    unknown = sorted({entry[SOURCE_KEY] for entry in entries if entry[SOURCE_KEY] not in known})
    if not unknown:
        return entries

    shown = ", ".join(repr(name) for name in unknown[:10])
    message = (
        f"{len(unknown)} 'source_doc' value(s) match no document in the corpus: "
        f"{shown}{' …' if len(unknown) > 10 else ''}"
    )
    if strict:
        raise ValueError(
            f"{message} — fix the question file, or pass --allow-unknown-docs "
            "to drop these questions instead."
        )
    kept = [entry for entry in entries if entry[SOURCE_KEY] in known]
    _log.warning("%s — dropping %d question(s).", message, len(entries) - len(kept))
    return kept


def _read_embedding_cache(cache_path: Path | None, embedding_model: str) -> dict[str, list[float]]:
    """Cached question embeddings, or {} if unusable.

    The model name is stored with the cache and checked: reusing vectors
    produced by a different embedding model would silently compare questions and
    documents from two different spaces.
    """
    if cache_path is None or not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.warning("Unreadable embedding cache (%s) — ignored.", cache_path)
        return {}
    if payload.get("embedding_model") != embedding_model:
        _log.warning(
            "Embedding cache built with model %r, current model is %r — ignored.",
            payload.get("embedding_model"), embedding_model,
        )
        return {}
    return payload.get("entries", {})


def _write_embedding_cache(
    cache_path: Path | None, embedding_model: str, entries: dict[str, list[float]]
) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"embedding_model": embedding_model, "entries": entries}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    except OSError:
        # A cache miss costs API calls, never correctness — don't fail the run.
        _log.warning("Could not write the embedding cache (%s).", cache_path, exc_info=True)


async def load_custom_questions(
    entries: list[dict],
    embeddings: AsyncEmbeddingClient,
    *,
    embedding_model: str = "",
    cache_path: Path | None = None,
) -> list[QuestionRecord]:
    """Embed an externally authored question set into ``QuestionRecord``s.

    Unlike the pipeline loaders, nothing is read from the document tree: the
    questions carry their own ground truth (``source_doc``) and their embeddings
    are computed here, so the same file can be scored against several corpora
    (baseline vs pipeline) without being copied into either.
    """
    cache = _read_embedding_cache(cache_path, embedding_model)
    records: list[QuestionRecord] = []
    hits = 0

    for idx, entry in enumerate(entries, start=1):
        text = entry[QUESTION_KEY]
        cached = cache.get(text)
        if cached is None:
            vector = np.array(await embeddings.get_embedding(text), dtype=np.float32)
            cache[text] = [float(value) for value in vector]
        else:
            vector = np.array(cached, dtype=np.float32)
            hits += 1

        records.append(QuestionRecord(
            question_idx=idx,
            content=text,
            source_doc_title=f"{entry[SOURCE_KEY]}.pdf",
            embedding=vector,
            slots={str(k): str(v) for k, v in (entry.get(SLOTS_KEY) or {}).items()},
            question_id=str(entry.get(ID_KEY) or f"q{idx:04d}"),
        ))

    _write_embedding_cache(cache_path, embedding_model, cache)
    _log.info(
        "Loaded %d custom question(s) — %d from cache, %d embedded.",
        len(records), hits, len(records) - hits,
    )
    return records
