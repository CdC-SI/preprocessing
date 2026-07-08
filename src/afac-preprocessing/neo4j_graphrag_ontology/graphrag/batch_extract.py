"""
batch_extract.py — Lance les 3 approches d'extraction (spaCy, VLM few-shot, VLM structured
output) sur tous les documents du corpus (mêmes dossiers que batch_build_kg.py), pour pouvoir
ensuite comparer/visualiser chaque document individuellement :
    compare_extractions.py --doc-name <X>
    visualize_extraction.py --doc-name <X>

Appelle directement les fonctions run() de chaque script (pas de sous-process) — même esprit
que batch_build_kg.py qui réutilise build_kg.run(). Une erreur sur un document/méthode
n'interrompt pas le batch (cf. batch_build_kg.py).

Usage :
    uv run python neo4j_graphrag_ontology/graphrag/batch_extract.py
    uv run python neo4j_graphrag_ontology/graphrag/batch_extract.py --method spacy   # rapide, hors-ligne, pas d'appel VLM
    uv run python neo4j_graphrag_ontology/graphrag/batch_extract.py --dotenv .env.test
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import extraction_spacy
import extraction_vlm_fewshot
import extraction_vlm_structured
from extraction_vlm_common import ALL_METHODS, DEFAULT_OUTPUT_DIR, extraction_json_path, list_documents

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("batch_extract")

_RUNNERS = {
    "spacy": lambda doc, dotenv, output_dir: extraction_spacy.run(doc, output_dir),
    "vlm_fewshot": lambda doc, dotenv, output_dir: extraction_vlm_fewshot.run(doc, dotenv, output_dir),
    "vlm_structured": lambda doc, dotenv, output_dir: extraction_vlm_structured.run(doc, dotenv, output_dir),
}


def run_one(doc_name: str, methods: tuple[str, ...], dotenv: str | None, output_dir: Path) -> dict[str, bool]:
    ok: dict[str, bool] = {}
    for method in methods:
        try:
            result = _RUNNERS[method](doc_name, dotenv, output_dir)
            result.to_json_file(extraction_json_path(doc_name, method))
            ok[method] = True
        except Exception as exc:  # noqa: BLE001 — on continue malgré l'échec d'une méthode/document
            _log.error("[%s] %s a échoué : %s", doc_name, method, exc)
            ok[method] = False
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Lance les 3 approches d'extraction sur tous les documents du corpus.")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--method", choices=(*ALL_METHODS, "all"), default="all",
                     help="Limiter à une seule méthode (ex. spacy pour un run rapide sans appel VLM).")
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    methods = ALL_METHODS if args.method == "all" else (args.method,)
    docs = list_documents(output_dir)
    _log.info("%d documents à traiter, méthodes : %s", len(docs), ", ".join(methods))

    summary: dict[str, dict[str, bool]] = {}
    for i, doc in enumerate(docs, 1):
        _log.info("[%d/%d] %s", i, len(docs), doc)
        summary[doc] = run_one(doc, methods, args.dotenv, output_dir)

    print(f"\n=== Résumé — {len(docs)} documents ===")
    for doc, results in summary.items():
        status = "  ".join(f"{m}:{'OK' if ok else 'ÉCHEC'}" for m, ok in results.items())
        print(f"{doc:50} {status}")

    n_failed = sum(1 for results in summary.values() for ok in results.values() if not ok)
    if n_failed:
        print(f"\n⚠️  {n_failed} échec(s) au total — voir les logs ci-dessus.")


if __name__ == "__main__":
    main()
