"""
extract_doc_concepts.py — Orchestration par document : combine les mots-clés TF-IDF
(keyword_extraction.py) et les concepts libres Qwen thinking (concept_extraction_llm.py)
dans un DocConcepts, écrit en JSON + Markdown lisible (extraction_concepts/output/).

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/extract_doc_concepts.py --doc-name Mineur
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/extract_doc_concepts.py --doc-name Mineur --dotenv .env.test
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
from compare_concepts import ConceptComparator  # noqa: E402
from concept_extraction_llm import ConceptLLMExtractor  # noqa: E402
from keyword_extraction import TOP_N_DEFAULT, KeywordExtractor  # noqa: E402
from schema import DocConcepts, Keyword, normalize_concepts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("extract_doc_concepts")

OUTPUT_DIR = THIS_DIR / "output"
THEME_DEFAULT = "Adhésion"


class DocConceptsExtractor:
    """Combine mots-clés (déjà calculés au niveau corpus) et concepts LLM pour un document.

    `keyword_extractor` est injecté (déjà fit sur le corpus) plutôt que reconstruit ici : le
    TF-IDF n'a de sens qu'au niveau corpus, un fit par document serait incorrect — voir
    batch_extract_concepts.py qui fit une fois puis boucle sur les documents.
    """

    def __init__(self, keyword_extractor: KeywordExtractor, llm_extractor: ConceptLLMExtractor, theme: str = THEME_DEFAULT) -> None:
        self._keyword_extractor = keyword_extractor
        self._llm_extractor = llm_extractor
        self._comparator = ConceptComparator()
        self.theme = theme

    def extract(self, doc_name: str, output_dir: Path = DEFAULT_OUTPUT_DIR, top_n_keywords: int = TOP_N_DEFAULT) -> DocConcepts:
        _log.info("[%s] === début extraction ===", doc_name)
        text = DocumentLocator(output_dir).resolve_final_md(doc_name).read_text(encoding="utf-8")

        _log.info("[%s] étape 1/3 — mots-clés TF-IDF...", doc_name)
        keywords: list[Keyword] = self._keyword_extractor.keywords_for(doc_name, top_n=top_n_keywords)
        _log.info("[%s] étape 1/3 — %d mots-clés retenus", doc_name, len(keywords))

        _log.info("[%s] étape 2/3 — concepts VLM (Qwen thinking)...", doc_name)
        vlm_concepts_raw = self._llm_extractor.extract(doc_name, output_dir)
        _log.info("[%s] étape 2/3 — %d concepts bruts", doc_name, len(vlm_concepts_raw))

        result = DocConcepts(
            doc_name=doc_name,
            theme=self.theme,
            char_count=len(text),
            spacy_keywords=keywords,
            vlm_concepts_raw=vlm_concepts_raw,
            vlm_concepts=normalize_concepts(vlm_concepts_raw),
        )
        _log.info("[%s] étape 3/3 — comparaison concepts/mots-clés...", doc_name)
        # Approche data-first : le recouvrement concepts/mots-clés est lui-même une donnée à
        # conserver (pas un affichage jetable recalculé à la demande), cf. schema.ConceptComparison.
        result.comparison = self._comparator.compare(result)
        _log.info("[%s] === extraction terminée — %d concepts, %d mots-clés ===", doc_name, len(result.vlm_concepts), len(result.spacy_keywords))
        return result

    @staticmethod
    def to_markdown(result: DocConcepts) -> str:
        lines = [f"# {result.doc_name}", "", f"Thème : {result.theme}", f"Caractères : {result.char_count}", ""]
        lines.append(f"## Mots-clés spaCy / TF-IDF ({len(result.spacy_keywords)})")
        lines.append("")
        lines.append("| Terme | Occurrences (ce doc) | Score TF-IDF | Doc. freq (corpus) |")
        lines.append("| :--- | ---: | ---: | ---: |")
        for kw in result.spacy_keywords:
            lines.append(f"| {kw.term} | {kw.count} | {kw.score:.4f} | {kw.doc_freq} |")
        lines.append("")
        lines.append(f"## Concepts VLM Qwen ({len(result.vlm_concepts)})")
        lines.append("")
        for c in result.vlm_concepts:
            lines.append(f"- {c}")

        if result.comparison:
            cmp = result.comparison
            lines.append("")
            lines.append("## Comparaison Concepts VLM / Mots-clés spaCy")
            lines.append("")
            lines.append(f"### Accord VLM ↔ spaCy ({len(cmp.agreement)}) — candidats haute confiance")
            lines.append("")
            for c in cmp.agreement:
                lines.append(f"- {c}")
            lines.append("")
            lines.append(f"### Concepts VLM sans écho spaCy — signal sémantique pur ({len(cmp.vlm_only)})")
            lines.append("")
            for c in cmp.vlm_only:
                lines.append(f"- {c}")
            lines.append("")
            lines.append(f"### Mots-clés spaCy sans écho VLM ({len(cmp.stat_only)}, bruit ou omission)")
            lines.append("")
            for t in cmp.stat_only:
                lines.append(f"- {t}")

        return "\n".join(lines) + "\n"

    def write(self, result: DocConcepts, output_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{result.doc_name}.json"
        md_path = output_dir / f"{result.doc_name}.md"
        result.to_json_file(json_path)
        md_path.write_text(self.to_markdown(result), encoding="utf-8")
        return json_path, md_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Extraction de concepts (mots-clés + LLM) sur un document AFAC.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--theme", default=THEME_DEFAULT)
    args = ap.parse_args()

    preprocessing_output_dir = Path(args.output_dir)
    keyword_extractor = KeywordExtractor(preprocessing_output_dir).fit_corpus_from_disk()
    llm_extractor = ConceptLLMExtractor(args.dotenv)

    extractor = DocConceptsExtractor(keyword_extractor, llm_extractor, theme=args.theme)
    result = extractor.extract(args.doc_name, preprocessing_output_dir)

    print(f"\n=== {result.doc_name} ({result.theme}) ===")
    print(f"{len(result.spacy_keywords)} mots-clés spaCy, {len(result.vlm_concepts)} concepts VLM Qwen")
    for c in result.vlm_concepts:
        print(f"  - {c}")

    json_path, md_path = extractor.write(result)
    print(f"\nÉcrit : {json_path}")
    print(f"Écrit : {md_path}")


if __name__ == "__main__":
    main()
