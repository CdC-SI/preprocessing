"""
test_graphrag_question.py — Petit test GraphRAG sur une question fixe, contre le graphe
"concept-guided" produit par build_kg_from_concepts.py.

Retrieval volontairement simple (pas de traduction NL -> Cypher) : le sous-graphe de test
(tag `test_source`) est petit, on le relit intégralement comme contexte plutôt que de passer
par un Text2CypherRetriever (neo4j_graphrag.retrievers) — piste à explorer plus tard une fois
le graphe stabilisé et le schéma assez régulier pour guider une traduction NL -> Cypher fiable.

Génération : UN appel VLM ASYNCHRONE (utils.vlm_client.text_completion_async), qui répond à la
question uniquement à partir des relations retrouvées dans le graphe.

Citations : chaque relation du contexte est annotée de son document source (`r.doc_name`,
posé par ConceptGraphLoader.load_triples) ; le VLM est instruit de citer ce document pour
chaque affirmation. Le chemin réel du fichier (PDF ou autre) est résolu via
SourceCitationResolver, qui le lit directement sur les nœuds `TextSource` créés par
LexicalGraphLoader (build_kg_from_concepts.py) — pas recalculé ici.

Validation par retrieval vectoriel : ChunkVectorValidator interroge l'index vectoriel officiel
(neo4j_graphrag.retrievers.VectorRetriever, sur l'index créé par
LexicalGraphLoader.ensure_vector_index()) pour afficher les top-k chunks les plus proches
sémantiquement de la question — un second angle de validation (texte brut) en complément du
contexte par triplets utilisé pour la génération.

Question de test par défaut (cf. discussion) :
    "Un étudiant (26 ans) ayant commencé ses études il y a 4 mois et résidant en Angleterre
    peut-il cotiser à l'AVS/AI facultative ?"
Une autre question peut être passée via --question.

Sauvegarde : AnswerRecorder écrit chaque run (question, contexte, sources, chunks, réponse)
dans un fichier Markdown horodaté sous qa_runs/ — nom unique (horodatage + slug de la question)
pour comparer plusieurs questions sans écraser les résultats précédents.

Usage :
    cd preprocessing/src/afac-preprocessing
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/test_graphrag_question.py \
        --theme Adhésion --dotenv .env.test

    # Avec une autre question :
    uv run --active python neo4j_graphrag_ontology/extraction_concepts/test_graphrag_question.py \
        --theme Adhésion --dotenv .env.test --question "GEDO sert à quoi ?"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import neo4j
from dotenv import load_dotenv
from neo4j_graphrag.retrievers import VectorRetriever
from neo4j_graphrag.types import RetrieverResultItem

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_kg_from_concepts import CHUNK_VECTOR_INDEX_NAME, source_tag  # noqa: E402
from shared.extraction_vlm_common import DOMAIN_CONTEXT  # noqa: E402
from shared.kg_shared_utils import EmbedderFactory  # noqa: E402
from utils.vlm_client import build_async_client, build_vlm_config, text_completion_async  # noqa: E402

QUESTION = (
    "Un étudiant (26 ans) ayant commencé ses études il y a 4 mois et résidant en Angleterre "
    "peut-il cotiser à l'AVS/AI facultative ?"
)

QA_RUNS_DIR = THIS_DIR / "qa_runs"  # une réponse sauvegardée par run, nom de fichier unique


def _slugify(text: str, max_len: int = 60) -> str:
    """Texte libre -> segment de nom de fichier lisible (ex. "Un étudiant (26 ans)..." ->
    "un_etudiant_26_ans"). Pas d'unicité en soi (deux questions proches donneraient le même
    slug) — combiné à l'horodatage dans AnswerRecorder pour l'unicité réelle."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return slug[:max_len].rstrip("_") or "question"


class AnswerRecorder:
    """Sauvegarde chaque run (question, contexte, sources, réponse) dans un fichier Markdown au
    nom UNIQUE (horodatage + slug de la question) — pour tester plusieurs questions sans écraser
    les résultats précédents, et comparer les runs a posteriori."""

    def __init__(self, output_dir: Path = QA_RUNS_DIR) -> None:
        self.output_dir = output_dir

    def save(
        self, *, theme: str, question: str, context: str, used_docs: list[str],
        source_paths: dict[str, str | None], chunk_results: list[dict], answer: str,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"{timestamp}_{_slugify(question)}.md"

        lines = [
            f"# GraphRAG — {theme}",
            "",
            f"**Horodatage** : {timestamp}",
            "",
            "## Question",
            "",
            question,
            "",
            f"## Documents source utilisés ({len(used_docs)})",
            "",
        ]
        lines += [f"- {doc} -> {source_paths.get(doc) or '(chemin introuvable)'}" for doc in used_docs]

        lines += ["", "## Contexte (triplets)", "", "```", context, "```"]

        if chunk_results:
            lines += ["", f"## Top-{len(chunk_results)} chunks (retrieval vectoriel)", ""]
            for i, chunk in enumerate(chunk_results, 1):
                score = chunk.get("score")
                lines += [
                    f"**[{i}] score={score:.4f} — doc={chunk.get('doc_name')} — chunk#{chunk.get('index')}**",
                    "",
                    chunk.get("text", ""),
                    "",
                ]

        lines += ["", "## Réponse", "", answer, ""]

        path.write_text("\n".join(lines), encoding="utf-8")
        return path


class GraphContextRetriever:
    """Récupère les triplets métier du sous-graphe de test (tag `test_source`), avec le document
    d'origine de chaque relation (`r.doc_name`, posé par ConceptGraphLoader.load_triples), pour
    servir de contexte ET de source citable à la génération. Isolé dans sa propre classe pour
    rester interchangeable — un Text2CypherRetriever ou un retriever vectoriel pourrait le
    remplacer sans toucher au reste du script."""

    def __init__(self, driver: neo4j.Driver, source: str) -> None:
        self.driver = driver
        self.source = source

    def fetch_triples(self) -> list[tuple[str, str, str, str]]:
        records, _, _ = self.driver.execute_query(
            """
            MATCH (s {test_source: $source})-[r {test_source: $source}]->(o {test_source: $source})
            RETURN s.name AS subject, r.predicate_fr AS predicate, o.name AS object, r.doc_name AS doc_name
            """,
            source=self.source,
        )
        return [(r["subject"], r["predicate"], r["object"], r["doc_name"]) for r in records]

    def as_context(self) -> str:
        triples = self.fetch_triples()
        if not triples:
            return "(aucun triplet trouvé pour ce test_source — le graphe a-t-il été construit ?)"
        return "\n".join(f"- {s} --[{p}]--> {o}  (source : {doc})" for s, p, o, doc in triples)

    def source_documents(self) -> list[str]:
        """Noms distincts des documents dont au moins une relation a contribué au contexte."""
        return sorted({doc for *_, doc in self.fetch_triples() if doc})


class SourceCitationResolver:
    """Résout, pour chaque document utilisé dans le contexte, le chemin du fichier source réel
    — lu directement sur les nœuds `TextSource` créés par LexicalGraphLoader
    (`build_kg_from_concepts.py`), pas recalculé ici : le graphe est la source de vérité pour la
    provenance."""

    def __init__(self, driver: neo4j.Driver, source: str) -> None:
        self.driver = driver
        self.source = source

    def resolve_all(self) -> dict[str, str | None]:
        records, _, _ = self.driver.execute_query(
            "MATCH (d:TextSource {test_source: $source}) RETURN d.name AS name, d.source_path AS path",
            source=self.source,
        )
        return {r["name"]: r["path"] for r in records}


def _format_chunk_result(record: neo4j.Record) -> RetrieverResultItem:
    """Formatteur custom pour VectorRetriever : le formatteur par défaut de la librairie fait
    `str(node)`, qui dumperait le vecteur d'embedding (1024 floats) en clair — on ne garde que
    ce qui sert à la validation (texte, document, position)."""
    node = record.get("node") or {}
    return RetrieverResultItem(
        content={"text": node.get("text"), "doc_name": node.get("doc_name"), "index": node.get("index")},
        metadata={"score": record.get("score")},
    )


class ChunkVectorValidator:
    """Retrieval top-k OFFICIEL (neo4j_graphrag.retrievers.VectorRetriever, sur l'index créé par
    LexicalGraphLoader.ensure_vector_index() dans build_kg_from_concepts.py) — affiche les chunks
    les plus proches sémantiquement de la question, pour vérifier visuellement que le graphe
    s'appuie sur les bons passages. Complète (ne remplace pas) le contexte par triplets utilisé
    pour la génération (GraphContextRetriever) : deux angles de validation, l'un structuré
    (relations), l'autre textuel brut (chunk source)."""

    def __init__(self, driver: neo4j.Driver, embedder, source: str, top_k: int = 5) -> None:
        self.retriever = VectorRetriever(
            driver, index_name=CHUNK_VECTOR_INDEX_NAME, embedder=embedder,
            return_properties=["text", "doc_name", "index"],
            result_formatter=_format_chunk_result,
        )
        self.source = source
        self.top_k = top_k

    def top_chunks(self, question: str) -> list[dict]:
        result = self.retriever.search(
            query_text=question, top_k=self.top_k, filters={"test_source": self.source}
        )
        return [{**item.content, "score": item.metadata.get("score")} for item in result.items]


class AsyncGraphRAGAnswerer:
    """Un seul appel VLM ASYNCHRONE pour répondre à la question à partir du contexte du graphe
    — pas de fabrication au-delà des relations fournies."""

    def __init__(self, client, model: str) -> None:
        self.client = client
        self.model = model

    @staticmethod
    def _build_prompt(context: str) -> str:
        return f"""{DOMAIN_CONTEXT}

Voici les relations extraites du graphe de connaissances pour ce thème, chacune annotée du \
document source dont elle provient :
{context}

Réponds à la question de l'utilisateur UNIQUEMENT à partir des relations ci-dessus. Pour \
chaque affirmation de ta réponse, cite entre parenthèses le document source correspondant (le \
nom donné après "source :" dans la relation utilisée) — ça doit permettre de vérifier chaque \
affirmation dans le document d'origine. Si le graphe ne contient pas assez d'information pour \
répondre avec certitude, dis-le explicitement et précise quelles relations manqueraient plutôt \
que d'inventer une réponse.

### IMPORTANT ###
Donne moi à la fin de ta réponse ton cheminement de raisonnement, en expliquant comment tu as utilisé les relations du graphe et les sources 
pour arriver à ta conclusion.
Si tu as dû faire des hypothèses, indique-les clairement.
"""

    async def answer(self, context: str, question: str) -> str:
        system_prompt = self._build_prompt(context)
        return await text_completion_async(self.client, self.model, system_prompt, question)


async def run(theme: str, dotenv: str, question: str = QUESTION) -> None:
    load_dotenv(dotenv)
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()

    source = source_tag(theme)
    retriever = GraphContextRetriever(driver, source)
    context = retriever.as_context()
    print(f"=== Contexte retrouvé (test_source={source!r}) ===")
    print(context)

    citation_resolver = SourceCitationResolver(driver, source)
    all_paths = citation_resolver.resolve_all()
    used_docs = retriever.source_documents()
    print(f"\n=== Documents source utilisés ({len(used_docs)}) ===")
    for doc in used_docs:
        path = all_paths.get(doc)
        print(f"  - {doc} -> {path or '(chemin introuvable — TextSource non créé pour ce doc)'}")

    cfg = build_vlm_config(Path(dotenv))

    embedder = EmbedderFactory(cfg).build()
    validator = ChunkVectorValidator(driver, embedder, source)
    chunk_results: list[dict] = []
    print(f"\n=== Top-{validator.top_k} chunks (retrieval vectoriel, pour validation) ===")
    try:
        chunk_results = validator.top_chunks(question)
        for i, chunk in enumerate(chunk_results, 1):
            score = chunk["score"]
            print(f"\n[{i}] score={score:.4f}  doc={chunk['doc_name']}  chunk#{chunk['index']}")
            print(chunk["text"])
    except neo4j.exceptions.ClientError as exc:
        print(f"(retrieval vectoriel indisponible : {exc}. "
              f"L'index « {CHUNK_VECTOR_INDEX_NAME} » existe-t-il ? "
              "Il est créé par build_kg_from_concepts.py — sans --no-embeddings.)")

    client = build_async_client(cfg)
    answerer = AsyncGraphRAGAnswerer(client, cfg.vlm_model_name)

    print(f"\n=== Question ===\n{question}")
    print("\n=== Réponse (appel VLM asynchrone) ===")
    answer = await answerer.answer(context, question)
    print(answer)

    recorder = AnswerRecorder()
    saved_path = recorder.save(
        theme=theme, question=question, context=context, used_docs=used_docs,
        source_paths=all_paths, chunk_results=chunk_results, answer=answer,
    )
    print(f"\n=== Sauvegardé ===\n{saved_path}")

    driver.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Test GraphRAG asynchrone sur une question.")
    ap.add_argument("--theme", default="Adhésion")
    ap.add_argument("--dotenv", default=".env.test")
    ap.add_argument("--question", default=QUESTION, help="Question à poser (défaut : la question de test fixe).")
    args = ap.parse_args()
    asyncio.run(run(args.theme, args.dotenv, args.question))


if __name__ == "__main__":
    main()
