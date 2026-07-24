"""
keyword_extraction.py — Mots-clés par TF-IDF sur l'ensemble du corpus Adhésion (20 docs).

Un score TF-IDF n'a de sens que relatif à un corpus (d'où le fit sur les 20 documents, pas
document par document) — répond au "mots les plus fréquents" du ticket 548.

PAS de lemmatisation : la lemmatisation spaCy s'est montrée peu fiable sur ce corpus (retour
d'équipe) — au-delà du cas déjà documenté CORRES -> "corre" (lemme inexistant), le
lemmatiseur statistique de fr_core_news_lg, hors de son vocabulaire entraîné sur du
vocabulaire métier AFAC, n'est pas assez fiable pour qu'on lui fasse confiance. On garde donc
la forme de surface (texte brut, minuscule), en ne gardant que les filtres POS/stopwords/
ponctuation/bruit — ceux-ci ne reposent pas sur le lemme, juste sur l'étiquetage grammatical.
Conséquence assumée : les variantes singulier/pluriel ("adhésion" / "adhésions") comptent
comme deux termes distincts, elles ne sont plus fusionnées automatiquement.

Réutilise le modèle spaCy déjà employé par graphrag/extraction_spacy.py (même famille de
modèle fr_core_news, cohérence avec l'approche NER existante) — seulement pour le POS
tagging/stopwords, plus pour la lemmatisation.

Usage :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/keyword_extraction.py
"""
from __future__ import annotations

import logging
import re
import sys
from collections import Counter
from pathlib import Path

import spacy
from sklearn.feature_extraction.text import TfidfVectorizer

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.extraction_vlm_common import DEFAULT_OUTPUT_DIR, DocumentLocator  # noqa: E402
from schema import Keyword  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("keyword_extraction")

SPACY_MODEL = "fr_core_news_lg"
KEPT_POS = {"NOUN", "PROPN", "ADJ"}
TOP_N_DEFAULT = 20

# Les _final.md contiennent des balises HTML résiduelles (ex. <span style="color:red">18
# ans</span>, cf. Mineur_final.md) que spaCy tague à tort comme NOUN/ADJ ("span", "color",
# "style", "red") — bruit pur, sans rapport avec le contenu métier. On les retire avant
# extraction plutôt que de les filtrer après coup par une liste de mots à exclure, plus
# fragile face à d'autres balises non encore rencontrées.
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Bruit récurrent issu du tableau d'historique de version présent dans l'en-tête de chaque
# aide-mémoire (lignes du type « | 1.5 | 17.03.2025 | ... | GT AM CORRES | ») : ce sont des
# codes de rôle/groupe de travail, pas du contenu métier. Liste à COMPLÉTER en observant le
# batch complet (esprit data-first : on ajoute au fil de ce que le corpus révèle).
_DOMAIN_STOPWORDS = frozenset({"gt", "am", "corres"})


class KeywordExtractor:
    """Fit un TF-IDF sur le corpus complet, puis restitue le top-N par document.

    Instanciation : `KeywordExtractor(output_dir)`. `fit(doc_texts)` une seule fois pour tout
    le corpus, puis `keywords_for(doc_name, top_n)` par document (pas de refit par appel).
    """

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR, spacy_model: str = SPACY_MODEL) -> None:
        self.output_dir = output_dir
        _log.info("Chargement du modèle spaCy %s...", spacy_model)
        self._nlp = spacy.load(spacy_model)
        self._vectorizer: TfidfVectorizer | None = None
        self._doc_names: list[str] = []
        self._tfidf_matrix = None
        self._doc_term_counts: list[Counter[str]] = []

    def _surface_terms(self, text: str) -> list[str]:
        """Unigrammes en FORME DE SURFACE (pas de lemme — cf. docstring module), filtrés par
        POS/stopwords/ponctuation/bruit domaine/chiffres. Le filtrage POS/stopwords vient de
        l'étiquetage grammatical de spaCy, pas du lemme — reste valable sans lemmatisation."""
        text = _HTML_TAG_RE.sub(" ", text)
        doc = self._nlp(text)
        terms = []
        for tok in doc:
            if (
                tok.pos_ not in KEPT_POS
                or tok.is_stop
                or tok.is_punct
                or len(tok.text) <= 1
                or tok.text.isdigit()
            ):
                continue
            term = tok.text.lower()
            if term in _DOMAIN_STOPWORDS:
                continue
            terms.append(term)
        return terms

    def _terms(self, text: str) -> list[str]:
        """Unigrammes + bigrammes (join par espace) — les bigrammes rendent la comparaison
        avec les concepts VLM (souvent multi-mots, ex. "date d'effet") like-for-like : un
        concept à deux mots ne peut structurellement pas matcher un vocabulaire d'unigrammes
        seuls. Bigrammes construits sur les termes déjà filtrés (pas sur le texte brut), donc
        cohérents avec le nettoyage POS/stopwords/bruit déjà appliqué."""
        unigrams = self._surface_terms(text)
        bigrams = [f"{a} {b}" for a, b in zip(unigrams, unigrams[1:])]
        return unigrams + bigrams

    def fit(self, doc_texts: dict[str, str]) -> "KeywordExtractor":
        """Calcule les termes (uni+bigrammes) de chaque document et fit le TF-IDF sur le corpus."""
        self._doc_names = list(doc_texts.keys())
        _log.info("Extraction des termes (uni+bigrammes) de %d documents (corpus TF-IDF) :", len(self._doc_names))
        term_docs = []
        for i, name in enumerate(self._doc_names, 1):
            terms = self._terms(doc_texts[name])
            _log.info("  [%d/%d] %s — %d termes retenus", i, len(self._doc_names), name, len(terms))
            term_docs.append(terms)
        self._doc_term_counts = [Counter(terms) for terms in term_docs]
        # analyzer=identité : les documents sont déjà tokenisés ci-dessus (uni+bigrammes joints
        # par espace). Laisser le tokenizer par défaut de TfidfVectorizer retraiter une chaîne
        # jointe re-scinderait les termes composés (ex. "e-mail" -> "e" + "mail" via son \w+
        # token_pattern, ou un bigramme "date effet" -> "date" + "effet"). ngram_range est
        # inutile ici : ignoré par sklearn quand analyzer est un callable, les bigrammes sont
        # déjà construits en amont dans _terms().
        self._vectorizer = TfidfVectorizer(analyzer=lambda tokens: tokens)
        self._tfidf_matrix = self._vectorizer.fit_transform(term_docs)
        _log.info("Fit TF-IDF terminé — vocabulaire de %d termes distincts", len(self._vectorizer.get_feature_names_out()))
        return self

    def keywords_for(self, doc_name: str, top_n: int = TOP_N_DEFAULT) -> list[Keyword]:
        """Top-N termes TF-IDF pour un document déjà présent dans le fit()."""
        if self._vectorizer is None or self._tfidf_matrix is None:
            raise RuntimeError("fit() doit être appelé avant keywords_for().")
        idx = self._doc_names.index(doc_name)
        vocab = self._vectorizer.get_feature_names_out()
        row = self._tfidf_matrix[idx].toarray().ravel()
        doc_freq = (self._tfidf_matrix > 0).sum(axis=0).A1  # nb de docs (corpus) où chaque terme apparaît
        term_counts = self._doc_term_counts[idx]  # nb d'occurrences DANS ce document

        # Sélection sur le score TF-IDF (les termes les plus distinctifs pour ce document,
        # pas juste les plus fréquents), mais tri d'affichage sur count (occurrences brutes,
        # du plus au moins fréquent dans ce document) — plus lisible pour une relecture
        # manuelle, cf. "les mots les plus fréquents" du ticket 548.
        top_indices = row.argsort()[::-1][:top_n]
        keywords = [
            Keyword(
                term=vocab[i],
                count=term_counts[vocab[i]],
                score=round(float(row[i]), 4),
                doc_freq=int(doc_freq[i]),
            )
            for i in top_indices
            if row[i] > 0
        ]
        keywords.sort(key=lambda kw: kw.count, reverse=True)
        return keywords

    def fit_corpus_from_disk(self) -> "KeywordExtractor":
        """Charge tous les _final.md du dossier de sortie et fit le TF-IDF dessus."""
        locator = DocumentLocator(self.output_dir)
        doc_names = locator.list_documents()
        _log.info("%d documents détectés dans %s : %s", len(doc_names), self.output_dir, doc_names)
        doc_texts = {name: locator.resolve_final_md(name).read_text(encoding="utf-8") for name in doc_names}
        return self.fit(doc_texts)


def main() -> None:
    extractor = KeywordExtractor().fit_corpus_from_disk()
    for doc_name in extractor._doc_names[:3]:
        keywords = extractor.keywords_for(doc_name)
        print(f"\n=== {doc_name} — top {len(keywords)} mots-clés ===")
        for kw in keywords:
            print(f"  {kw.term:20} count={kw.count:3}  score={kw.score:.4f}  doc_freq={kw.doc_freq}")


if __name__ == "__main__":
    main()
