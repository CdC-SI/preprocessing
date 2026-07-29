"""
batch_extract_concepts.py — Lance l'extraction de concepts (mots-clés + LLM) sur tous les
documents du corpus Adhésion (mêmes dossiers que graphrag/batch_build_kg.py). Fit le TF-IDF
une seule fois sur le corpus complet, puis boucle sur les documents pour la partie LLM.

Une erreur d'extraction LLM sur un document n'interrompt pas le batch (même esprit que
graphrag/batch_extract.py) — le document est loggé en échec, le batch continue.

THEME est une constante locale car un seul thème est prétraité aujourd'hui (Adhésion, 20
docs) — l'ontologie ne porte pas encore de mapping document → thème générique, inutile de
l'anticiper avant que d'autres thèmes soient prétraités.

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/batch_extract_concepts.py
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/batch_extract_concepts.py --dotenv .env.test
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.extraction_vlm_common import DEFAULT_OUTPUT_DIR, DocumentLocator  # noqa: E402
from concept_extraction_llm import ConceptLLMExtractor  # noqa: E402
from extract_doc_concepts import OUTPUT_DIR, THEME_DEFAULT, DocConceptsExtractor  # noqa: E402
from keyword_extraction import KeywordExtractor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("batch_extract_concepts")


class BatchConceptExtractor:
    """Fit le TF-IDF une fois sur le corpus, puis extrait chaque document (mots-clés + LLM)."""

    def __init__(self, dotenv: str | None, output_dir: Path = DEFAULT_OUTPUT_DIR, theme: str = THEME_DEFAULT) -> None:
        self.output_dir = output_dir
        self.theme = theme
        keyword_extractor = KeywordExtractor(output_dir).fit_corpus_from_disk()
        llm_extractor = ConceptLLMExtractor(dotenv)
        self._doc_extractor = DocConceptsExtractor(keyword_extractor, llm_extractor, theme=theme)

    def run(self, write_dir: Path = OUTPUT_DIR) -> dict[str, bool]:
        docs = DocumentLocator(self.output_dir).list_documents()
        _log.info("%d documents à traiter (thème %s)", len(docs), self.theme)

        summary: dict[str, bool] = {}
        for i, doc_name in enumerate(docs, 1):
            _log.info("[%d/%d] %s", i, len(docs), doc_name)
            try:
                result = self._doc_extractor.extract(doc_name, self.output_dir)
                self._doc_extractor.write(result, write_dir)
                summary[doc_name] = True
            except Exception:  # noqa: BLE001 — on continue malgré l'échec d'un document
                _log.exception("[%s] échec", doc_name)
                summary[doc_name] = False
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Lance l'extraction de concepts sur tous les documents du corpus Adhésion.")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--theme", default=THEME_DEFAULT)
    args = ap.parse_args()

    batch = BatchConceptExtractor(args.dotenv, Path(args.output_dir), args.theme)
    summary = batch.run()

    print(f"\n=== Résumé — {len(summary)} documents ===")
    for doc, ok in summary.items():
        print(f"{doc:50} {'OK' if ok else 'ÉCHEC'}")

    n_failed = sum(1 for ok in summary.values() if not ok)
    if n_failed:
        print(f"\n {n_failed} échec(s) au total — voir les logs ci-dessus.")


if __name__ == "__main__":
    main()
