"""
compare_concepts.py — Compare le signal sémantique (vlm_concepts, Qwen thinking) et le signal
statistique (spacy_keywords, TF-IDF uni+bigrammes) d'un même document, à partir du JSON déjà
écrit par extract_doc_concepts.py. Répartit chaque terme dans 3 seaux exclusifs (agreement /
vlm_only / stat_only) — cf. schema.ConceptComparison.

Les deux méthodes sont totalement indépendantes (aucune ne guide l'autre, ni n'est guidée
par ontology/afac_ontology.py — cf. concept_extraction_llm.py et schema.py). La comparaison
porte sur le texte de surface, comme graphrag/compare_extractions.py, mais avec une règle
LIKE-FOR-LIKE : un concept VLM multi-mots (ex. "Confirmation d'adhésion") n'est en accord que
si un BIGRAMME entier de spacy_keywords le corrobore, jamais un simple mot partagé au hasard
(ex. "Domicile en Suisse" ne doit pas matcher juste parce que "suisse" est fréquent — cf.
_matches()). Un concept d'un seul mot reste comparé mot à mot, faute de bigramme à construire.

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/compare_concepts.py --doc-name Mineur
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from schema import ConceptComparison, DocConcepts  # noqa: E402

# Défini localement (pas importé depuis extract_doc_concepts.py) pour éviter un import
# circulaire : extract_doc_concepts.py importe ConceptComparator de ce module pour persister
# la comparaison dans DocConcepts.comparison (cf. schema.py).
OUTPUT_DIR = THIS_DIR / "output"

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Élisions françaises (d', l', n', s', t', c', j', m', qu') : \w+ les découperait en mot à
# part entière ("d'adhésion" -> "d", "adhésion"), cassant le bigramme "confirmation adhésion"
# attendu pour "Confirmation d'adhésion". Côté spaCy, ces particules sont des stopwords déjà
# filtrés (cf. keyword_extraction._lemmatize) — on retire l'élision ici pour rester
# like-for-like avec la sortie spaCy plutôt que de la traiter comme un mot.
_ELISION_RE = re.compile(r"\b[a-zà-öù-ÿ]{1,2}'", re.IGNORECASE | re.UNICODE)


def _word_list(text: str) -> list[str]:
    """Mots en ordre (pas un set) — nécessaire pour construire des bigrammes consécutifs."""
    text = _ELISION_RE.sub("", text)
    return [w.lower() for w in _WORD_RE.findall(text)]


def _words(text: str) -> set[str]:
    return set(_word_list(text))


def _bigrams(text: str) -> set[str]:
    """Paires de mots consécutifs joints par espace — même format que les bigrammes produits
    côté spaCy (cf. keyword_extraction.KeywordExtractor._terms), pour un match direct."""
    words = _word_list(text)
    return {f"{a} {b}" for a, b in zip(words, words[1:])}


class ConceptComparator:
    """Compare, pour un document, les concepts Qwen et les mots-clés TF-IDF (uni+bigrammes) —
    lecture seule d'un DocConcepts déjà écrit sur disque (extract_doc_concepts.py /
    batch_extract_concepts.py). Voir _matches() pour la règle like-for-like."""

    def __init__(self, docs_dir: Path = OUTPUT_DIR) -> None:
        self.docs_dir = docs_dir

    def load(self, doc_name: str) -> DocConcepts:
        path = self.docs_dir / f"{doc_name}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} absent — lancer extract_doc_concepts.py --doc-name {doc_name} d'abord.")
        return DocConcepts.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _matches(concept: str, keyword_terms: set[str]) -> bool:
        """Un concept d'un seul mot est comparé mot à mot ; un concept multi-mots exige un
        bigramme entier partagé — évite le faux-accord sur un mot isolé d'une expression."""
        words = _word_list(concept)
        if not words:
            return False
        if len(words) == 1:
            return words[0] in keyword_terms
        return bool(_bigrams(concept) & keyword_terms)

    def compare(self, result: DocConcepts) -> ConceptComparison:
        keyword_terms = {kw.term.lower() for kw in result.spacy_keywords}

        agreement, vlm_only = [], []
        for concept in result.vlm_concepts:
            (agreement if self._matches(concept, keyword_terms) else vlm_only).append(concept)

        # Symétrique : un mot-clé unigramme est "couvert" s'il apparaît comme mot dans un
        # concept ; un mot-clé bigramme ne l'est que s'il apparaît comme bigramme entier.
        concept_unigrams: set[str] = set()
        concept_bigrams: set[str] = set()
        for concept in result.vlm_concepts:
            concept_unigrams |= _words(concept)
            concept_bigrams |= _bigrams(concept)

        stat_only = sorted(
            term for term in keyword_terms
            if (term not in concept_bigrams if " " in term else term not in concept_unigrams)
        )

        return ConceptComparison(agreement=agreement, vlm_only=vlm_only, stat_only=stat_only)


def print_report(doc_name: str, comparison: ConceptComparison) -> None:
    print(f"\n=== Comparaison Concepts VLM Qwen / Mots-clés spaCy — {doc_name} ===")

    if comparison.agreement:
        print(f"\nAccord VLM ↔ spaCy ({len(comparison.agreement)}) — candidats haute confiance pour l'ontologie :")
        for c in comparison.agreement:
            print(f"    ✓ {c}")
    if comparison.vlm_only:
        print(f"\nConcepts VLM sans écho spaCy — signal sémantique pur ({len(comparison.vlm_only)}) :")
        for c in comparison.vlm_only:
            print(f"    - {c}")
    if comparison.stat_only:
        print(f"\nMots-clés spaCy sans écho VLM ({len(comparison.stat_only)}, bruit ou omission à surveiller) :")
        print(f"    {comparison.stat_only}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare concepts (Qwen) et mots-clés (TF-IDF) pour un document.")
    ap.add_argument("--doc-name", default="Mineur")
    ap.add_argument("--docs-dir", default=str(OUTPUT_DIR))
    args = ap.parse_args()

    comparator = ConceptComparator(Path(args.docs_dir))
    result = comparator.load(args.doc_name)
    comparison = comparator.compare(result)
    print_report(args.doc_name, comparison)


if __name__ == "__main__":
    main()
