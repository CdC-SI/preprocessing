"""
aggregate_theme_concepts.py — Passe "remontée" du ticket 548 : lit les DocConcepts déjà
écrits par batch_extract_concepts.py, regroupe par thème. Produit un support de relecture
manuelle pour valider ou compléter les concepts généraux du thème — pas de restructuration
automatique des sous-dossiers, la décision reste manuelle.

Fait remonter 3 vues au niveau thème, alors qu'avant seul le rollup VLM (`concepts`)
existait à ce niveau (le signal data-first et la comparaison s'arrêtaient au document) :
  - `keyword_rollup`   : pendant data-first de `concepts` — mots-clés spaCy agrégés sur le thème.
  - `theme_agreement`  : concepts en accord VLM/stat dans au moins un doc du thème.
  - `theme_vlm_only` / `theme_stat_only` : jamais corroborés sur TOUT le thème (pas juste un doc).

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/aggregate_theme_concepts.py
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

from .extract_doc_concepts import OUTPUT_DIR  # noqa: E402
from .schema import DocConcepts, ThemeConceptRow, ThemeConcepts, ThemeKeywordRow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("aggregate_theme_concepts")


class ThemeConceptAggregator:
    """Regroupe les DocConcepts d'un dossier de sortie par thème, avec couverture documentaire
    par concept (nombre de documents distincts où le concept apparaît, triée décroissante)."""

    def __init__(self, docs_dir: Path = OUTPUT_DIR) -> None:
        self.docs_dir = docs_dir

    def _load_doc_concepts(self) -> list[DocConcepts]:
        # Exclut les _theme_*.json : ce sont les rollups déjà écrits par ce même script lors
        # d'un run précédent (ThemeConcepts, pas DocConcepts) — les inclure ferait planter
        # model_validate() sur un shape différent.
        json_paths = sorted(p for p in self.docs_dir.glob("*.json") if not p.name.startswith("_theme_"))
        _log.info("%d fichiers DocConcepts trouvés dans %s", len(json_paths), self.docs_dir)
        results = []
        for json_path in json_paths:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            results.append(DocConcepts.model_validate(data))
            _log.info("  chargé : %s", json_path.name)
        return results

    @staticmethod
    def _concept_rollup(theme_docs: list[DocConcepts]) -> list[ThemeConceptRow]:
        """Couverture documentaire de chaque vlm_concept — rollup VLM historique."""
        coverage: dict[str, list[str]] = defaultdict(list)
        for doc in theme_docs:
            for concept in doc.vlm_concepts:
                coverage[concept].append(doc.doc_name)
        rows = [ThemeConceptRow(name=name, doc_coverage=len(docs), docs=sorted(docs)) for name, docs in coverage.items()]
        rows.sort(key=lambda r: (-r.doc_coverage, r.name.lower()))
        return rows

    @staticmethod
    def _keyword_rollup(theme_docs: list[DocConcepts]) -> list[ThemeKeywordRow]:
        """Pendant data-first de _concept_rollup — absent avant cette passe : le signal spaCy
        s'arrêtait au document."""
        coverage: dict[str, list[str]] = defaultdict(list)
        total_count: dict[str, int] = defaultdict(int)
        for doc in theme_docs:
            for kw in doc.spacy_keywords:
                term = kw.term.lower()
                coverage[term].append(doc.doc_name)
                total_count[term] += kw.count
        rows = [
            ThemeKeywordRow(term=term, doc_coverage=len(docs), total_count=total_count[term])
            for term, docs in coverage.items()
        ]
        rows.sort(key=lambda r: (-r.doc_coverage, -r.total_count, r.term))
        return rows

    @staticmethod
    def _agreement_rollup(theme_docs: list[DocConcepts]) -> tuple[list[ThemeConceptRow], list[str], list[str]]:
        """Agrège les 3 seaux de ConceptComparison (par doc) au niveau thème :
          - theme_agreement : concepts en accord dans au moins un doc (avec couverture).
          - theme_vlm_only  : concepts JAMAIS en accord sur tout le thème (exclut tout concept
            qui a atteint l'accord dans un autre doc — "jamais" porte sur le thème entier).
          - theme_stat_only : termes stat restés stat_only dans CHAQUE doc où ils apparaissaient
            comme mot-clé (jamais rattrapés par un accord ailleurs sur le thème).
        """
        agreement_coverage: dict[str, list[str]] = defaultdict(list)
        vlm_only_seen: set[str] = set()
        for doc in theme_docs:
            if not doc.comparison:
                continue
            for concept in doc.comparison.agreement:
                agreement_coverage[concept].append(doc.doc_name)
            vlm_only_seen.update(doc.comparison.vlm_only)

        theme_agreement = [
            ThemeConceptRow(name=name, doc_coverage=len(docs), docs=sorted(docs))
            for name, docs in agreement_coverage.items()
        ]
        theme_agreement.sort(key=lambda r: (-r.doc_coverage, r.name.lower()))
        theme_vlm_only = sorted(c for c in vlm_only_seen if c not in agreement_coverage)

        term_docs_seen: dict[str, int] = defaultdict(int)
        term_docs_stat_only: dict[str, int] = defaultdict(int)
        for doc in theme_docs:
            if not doc.comparison:
                continue
            doc_terms = {kw.term.lower() for kw in doc.spacy_keywords}
            stat_only_set = set(doc.comparison.stat_only)
            for term in doc_terms:
                term_docs_seen[term] += 1
                if term in stat_only_set:
                    term_docs_stat_only[term] += 1
        theme_stat_only = sorted(t for t, seen in term_docs_seen.items() if term_docs_stat_only[t] == seen)

        return theme_agreement, theme_vlm_only, theme_stat_only

    def aggregate(self) -> dict[str, ThemeConcepts]:
        docs = self._load_doc_concepts()

        by_theme: dict[str, list[DocConcepts]] = defaultdict(list)
        for doc in docs:
            by_theme[doc.theme].append(doc)
        _log.info("%d thème(s) détecté(s) : %s", len(by_theme), {t: len(d) for t, d in by_theme.items()})

        result: dict[str, ThemeConcepts] = {}
        for theme, theme_docs in by_theme.items():
            theme_agreement, theme_vlm_only, theme_stat_only = self._agreement_rollup(theme_docs)
            result[theme] = ThemeConcepts(
                theme=theme,
                doc_count=len(theme_docs),
                source_documents=sorted(doc.doc_name for doc in theme_docs),
                concepts=self._concept_rollup(theme_docs),
                keyword_rollup=self._keyword_rollup(theme_docs),
                theme_agreement=theme_agreement,
                theme_vlm_only=theme_vlm_only,
                theme_stat_only=theme_stat_only,
            )
        return result

    @staticmethod
    def to_markdown(theme_concepts: ThemeConcepts) -> str:
        lines = [
            f"# Concepts du thème {theme_concepts.theme}",
            "",
            f"{theme_concepts.doc_count} documents, {len(theme_concepts.concepts)} concepts distincts",
            "",
            "## Documents source",
            "",
        ]
        lines += [f"- {name}" for name in theme_concepts.source_documents]

        lines += [
            "",
            f"## Concepts VLM Qwen, rollup thème ({len(theme_concepts.concepts)})",
            "",
            "| Concept | # docs | Documents |",
            "| :--- | ---: | :--- |",
        ]
        for row in theme_concepts.concepts:
            lines.append(f"| {row.name} | {row.doc_coverage} | {', '.join(row.docs)} |")

        lines += [
            "",
            f"## Mots-clés spaCy / TF-IDF, rollup thème ({len(theme_concepts.keyword_rollup)}) — pendant data-first de la section précédente",
            "",
            "| Terme | # docs | Occurrences totales |",
            "| :--- | ---: | ---: |",
        ]
        for kw_row in theme_concepts.keyword_rollup:
            lines.append(f"| {kw_row.term} | {kw_row.doc_coverage} | {kw_row.total_count} |")

        lines += [
            "",
            f"## Accord VLM ↔ spaCy sur le thème ({len(theme_concepts.theme_agreement)}) — candidats haute confiance pour l'ontologie",
            "",
            "| Concept | # docs | Documents |",
            "| :--- | ---: | :--- |",
        ]
        for row in theme_concepts.theme_agreement:
            lines.append(f"| {row.name} | {row.doc_coverage} | {', '.join(row.docs)} |")

        lines += [
            "",
            f"## Concepts VLM jamais corroborés par spaCy sur le thème — signal sémantique pur ({len(theme_concepts.theme_vlm_only)})",
            "",
        ]
        lines += [f"- {c}" for c in theme_concepts.theme_vlm_only]

        lines += [
            "",
            f"## Mots-clés spaCy jamais retrouvés dans un concept VLM sur le thème ({len(theme_concepts.theme_stat_only)}, bruit ou omission)",
            "",
        ]
        lines += [f"- {t}" for t in theme_concepts.theme_stat_only]

        return "\n".join(lines) + "\n"

    def write(self, theme_concepts: ThemeConcepts, output_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_theme = theme_concepts.theme.replace("/", "_")
        json_path = output_dir / f"_theme_{safe_theme}.json"
        md_path = output_dir / f"_theme_{safe_theme}.md"
        theme_concepts.to_json_file(json_path)
        md_path.write_text(self.to_markdown(theme_concepts), encoding="utf-8")
        return json_path, md_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Agrège les DocConcepts par thème (rollup pour relecture manuelle).")
    ap.add_argument("--docs-dir", default=str(OUTPUT_DIR))
    args = ap.parse_args()

    aggregator = ThemeConceptAggregator(Path(args.docs_dir))
    by_theme = aggregator.aggregate()

    for theme, theme_concepts in by_theme.items():
        print(f"\n=== {theme} — {theme_concepts.doc_count} docs, {len(theme_concepts.concepts)} concepts VLM Qwen ===")
        print(f"\nAccord VLM ↔ spaCy ({len(theme_concepts.theme_agreement)}) — candidats haute confiance :")
        for row in theme_concepts.theme_agreement[:20]:
            print(f"  {row.doc_coverage:3} docs  {row.name}")
        print(f"\nMots-clés spaCy, rollup thème ({len(theme_concepts.keyword_rollup)}) :")
        for kw_row in theme_concepts.keyword_rollup[:20]:
            print(f"  {kw_row.doc_coverage:3} docs  {kw_row.term} (x{kw_row.total_count})")
        if theme_concepts.theme_vlm_only:
            print(f"\nConcepts VLM jamais corroborés par spaCy ({len(theme_concepts.theme_vlm_only)}) : {theme_concepts.theme_vlm_only[:10]}")
        if theme_concepts.theme_stat_only:
            print(f"\nMots-clés spaCy jamais retrouvés dans un concept VLM ({len(theme_concepts.theme_stat_only)}) : {theme_concepts.theme_stat_only[:10]}")

        json_path, md_path = aggregator.write(theme_concepts)
        print(f"\nÉcrit : {json_path}")
        print(f"Écrit : {md_path}")


if __name__ == "__main__":
    main()
