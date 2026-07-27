"""
test_graphrag_question.py — Petit test GraphRAG sur une question fixe, contre le graphe
"concept-guided" produit par build_kg_from_concepts.py.

Retrieval par VectorCypherRetriever (neo4j_graphrag.retrievers) : la question est d'abord
embeddée pour retrouver les chunks les plus proches sémantiquement (même index vectoriel que
ChunkVectorValidator), puis un template Cypher FIXE (jamais généré par un LLM) part des entités
ancrées à ces chunks (FROM_CHUNK) et étend à profondeur bornée (hop_depth) pour ne remonter que
les triplets pertinents à la question — plutôt que tout le sous-graphe `test_source` comme
auparavant (664 triplets sur le thème Adhésion, sans lien avec la question posée). Un
Text2CypherRetriever reste une piste pour plus tard (cf. PLAN_vector_cypher_retriever.md,
section "Future work"), une fois le vocabulaire des prédicats canonicalisé.

Génération : UN appel VLM ASYNCHRONE (utils.vlm_client.text_completion_async), qui répond à la
question uniquement à partir des relations retrouvées dans le graphe.

Citations : chaque relation du contexte est annotée de son document source (`r.doc_name`,
posé par ConceptGraphLoader.load_triples) ; le VLM est instruit de citer ce document pour
chaque affirmation. Le chemin réel du fichier (PDF ou autre) est résolu via
SourceCitationResolver, qui le lit directement sur les nœuds `TextSource` créés par
LexicalGraphLoader (build_kg_from_concepts.py) — pas recalculé ici.

Contexte hybride (triplets + texte brut) : en plus des triplets, ChunkVectorValidator interroge
l'index vectoriel officiel (neo4j_graphrag.retrievers.VectorRetriever, sur l'index créé par
LexicalGraphLoader.ensure_vector_index()) pour retrouver les top-5 chunks les plus proches
sémantiquement de la question — leur texte brut est envoyé au VLM en complément des triplets
(AsyncGraphRAGAnswerer), pas seulement affiché : les triplets, extraits par un VLM avec un
vocabulaire de prédicats libre (cf. PLAN_vector_cypher_retriever.md), peuvent lisser des nuances,
exceptions ou valeurs précises que le texte source d'origine conserve intact.

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
from neo4j_graphrag.retrievers import VectorCypherRetriever, VectorRetriever
from neo4j_graphrag.types import RetrieverResultItem

THIS_DIR = Path(__file__).resolve().parent            # .../extraction_concepts
KG_DIR = THIS_DIR.parent                                # .../neo4j_graphrag_ontology
PROJECT_ROOT = KG_DIR.parent                            # .../afac-preprocessing
for p in (str(PROJECT_ROOT), str(KG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_kg_from_concepts import ANCHOR_LABEL, CHUNK_VECTOR_INDEX_NAME, source_tag  # noqa: E402
from shared.extraction_vlm_common import DOMAIN_CONTEXT  # noqa: E402
from shared.kg_shared_utils import EmbedderFactory  # noqa: E402
from utils.vlm_client import build_async_client, build_vlm_config, text_completion_async  # noqa: E402

QUESTION = (
    "Un étudiant (26 ans) ayant commencé ses études il y a 4 mois et résidant en Angleterre "
    "peut-il cotiser à l'AVS/AI facultative?"
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


def _format_triple_result(record: neo4j.Record) -> RetrieverResultItem:
    """Formatteur custom pour VectorCypherRetriever : chaque ligne renvoyée par retrieval_query
    est déjà un triplet aplati (une relation traversée depuis un chunk-ancre), pas un nœud brut.
    `seed_score` (score vectoriel du chunk-ancre à l'origine de cette ligne) est conservé pour
    permettre un tri par pertinence côté Python plutôt qu'une troncature dans un ordre arbitraire."""
    return RetrieverResultItem(
        content={
            "subject": record.get("subject"),
            "predicate": record.get("predicate"),
            "object": record.get("object"),
            "doc_name": record.get("doc_name"),
            "seed_score": record.get("seed_score"),
        },
    )


class VectorGraphContextRetriever:
    """Retrouve un sous-ensemble de triplets PERTINENTS À LA QUESTION (et non plus tout le
    sous-graphe test_source) : recherche vectorielle sur les chunks (top-k), puis expansion
    Cypher à profondeur bornée depuis les entités ancrées à ces chunks (FROM_CHUNK), filtrée sur
    test_source à chaque saut. Remplace l'ancien dump complet (GraphContextRetriever) — cf.
    PLAN_vector_cypher_retriever.md. Isolé dans sa propre classe pour rester interchangeable,
    comme son prédécesseur.

    Pas de plafond par chunk-ancre (contrairement à une version précédente) : un chunk dense
    (qui ancre beaucoup d'entités à la fois) regroupait leurs chemins avant troncature, dans un
    ORDRE ARBITRAIRE (celui de collect() côté Cypher) — ça a supprimé silencieusement des faits
    déterminants sur une vraie question de test (la condition des 5 ans d'assurance préalable),
    faisant basculer la réponse du LLM. Seul `max_total_triples` plafonne désormais, et le fait
    dans un ordre choisi (score du chunk-ancre le plus pertinent par triplet), pas arbitraire."""

    def __init__(
        self, driver: neo4j.Driver, embedder, source: str, *,
        seed_top_k: int = 8, hop_depth: int = 1, max_total_triples: int = 200,
    ) -> None:
        if hop_depth < 1:
            raise ValueError("hop_depth doit être >= 1")
        self.source = source
        self.seed_top_k = seed_top_k
        self.max_total_triples = max_total_triples
        self.retriever = VectorCypherRetriever(
            driver, index_name=CHUNK_VECTOR_INDEX_NAME,
            retrieval_query=self._build_retrieval_query(hop_depth),
            embedder=embedder, result_formatter=_format_triple_result,
        )
        self._last_triples: list[tuple[str, str, str, str]] = []

    @staticmethod
    def _build_retrieval_query(hop_depth: int) -> str:
        # hop_depth : constante côté Python (config, jamais dérivée de la question) — splicée en
        # littéral, PAS en paramètre Cypher : les bornes de longueur de relation variable ne sont
        # pas fiablement paramétrables à l'exécution.
        return f"""
        WITH node AS chunk, score
        MATCH (seed:{ANCHOR_LABEL} {{test_source: $source}})-[:FROM_CHUNK]->(chunk)
        MATCH (seed)-[rels*1..{hop_depth}]-(other:{ANCHOR_LABEL} {{test_source: $source}})
        WHERE ALL(r IN rels WHERE r.test_source = $source)
        UNWIND rels AS r
        WITH DISTINCT chunk, score, r
        RETURN startNode(r).name AS subject, r.predicate_fr AS predicate,
               endNode(r).name AS object, r.doc_name AS doc_name, score AS seed_score
        """

    def fetch_triples(self, question: str) -> list[tuple[str, str, str, str]]:
        # Pas de filters= ici : neo4j-graphrag 1.18.0 a un bug sur VectorCypherRetriever (le
        # champ embedding_node_property n'est jamais peuplé — attribut interne mal nommé lors
        # de la lecture des métadonnées d'index, cf. _fetch_index_infos), qui fait échouer
        # TOUTE recherche avec filters= sur ce retriever (indépendant de notre configuration).
        # Sans incidence : le scoping par thème est déjà garanti par $source dans le
        # retrieval_query, et les chunks sont de toute façon exclusifs à un seul test_source.
        result = self.retriever.search(
            query_text=question, top_k=self.seed_top_k, query_params={"source": self.source},
        )
        best_score: dict[tuple[str, str, str, str], float] = {}
        for item in result.items:
            c = item.content
            key = (c["subject"], c["predicate"], c["object"], c["doc_name"])
            if not all(key):
                continue
            score = c.get("seed_score") or 0.0
            if key not in best_score or score > best_score[key]:
                best_score[key] = score
        # Tri par pertinence (meilleur score de chunk-ancre ayant produit ce triplet) : si une
        # troncature est nécessaire, elle retire les triplets les MOINS pertinents en premier,
        # plutôt qu'un sous-ensemble arbitraire.
        triples = sorted(best_score, key=lambda k: best_score[k], reverse=True)
        if len(triples) > self.max_total_triples:
            print(f"(avertissement : {len(triples)} triplets dédoublonnés, tronqué à "
                  f"{self.max_total_triples} les plus pertinents — augmenter --max-triples si besoin)")
            triples = triples[: self.max_total_triples]
        self._last_triples = triples
        return triples

    def as_context(self, question: str) -> str:
        triples = self.fetch_triples(question)
        if not triples:
            return "(aucun triplet pertinent trouvé pour cette question — le graphe a-t-il été construit ?)"
        return "\n".join(f"- {s} --[{p}]--> {o}  (source : {doc})" for s, p, o, doc in triples)

    def source_documents(self) -> list[str]:
        """Repose sur le dernier fetch_triples()/as_context() exécuté (mis en cache) — évite de
        relancer une recherche vectorielle rien que pour lister les documents."""
        return sorted({doc for *_, doc in self._last_triples if doc})


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
    LexicalGraphLoader.ensure_vector_index() dans build_kg_from_concepts.py) — les chunks les
    plus proches sémantiquement de la question. Sert DEUX rôles : (1) affichage/validation
    (vérifier visuellement que le graphe s'appuie sur les bons passages) et (2), depuis
    l'ajout du texte brut au contexte de génération, fournit aussi les extraits envoyés au VLM
    en complément des triplets (VectorGraphContextRetriever) — les triplets perdent forcément
    des nuances lors de l'extraction (dates, délais, exceptions précises) que le texte source
    brut conserve intact. Résultat déjà calculé une seule fois dans run() et réutilisé pour les
    deux rôles, pas de requête vectorielle supplémentaire."""

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
    (triplets) ET des extraits de texte source (chunks) qui ont servi à ancrer la recherche —
    pas de fabrication au-delà de ce qui est fourni. Les deux sources sont volontairement
    distinguées dans le prompt : les triplets portent le fil de raisonnement structuré
    (citable relation par relation), le texte brut rattrape les nuances/valeurs précises que
    l'extraction en triplets a pu lisser ou perdre (cf. discussion sur le vocabulaire de
    prédicats non canonicalisé — PLAN_vector_cypher_retriever.md)."""

    def __init__(self, client, model: str) -> None:
        self.client = client
        self.model = model

    @staticmethod
    def _format_chunk_excerpts(chunk_results: list[dict]) -> str:
        if not chunk_results:
            return "(aucun extrait de texte source disponible)"
        return "\n\n".join(
            f"[Document : {c.get('doc_name')}]\n{c.get('text', '')}" for c in chunk_results
        )

    @staticmethod
    def _build_prompt(context: str, chunk_results: list[dict]) -> str:
        excerpts = AsyncGraphRAGAnswerer._format_chunk_excerpts(chunk_results)
        return f"""{DOMAIN_CONTEXT}

Voici les relations extraites du graphe de connaissances pour ce thème, chacune annotée du \
document source dont elle provient :
{context}

Voici en complément des extraits de texte source brut (les passages documentaires qui ont \
ancré cette recherche) — ils peuvent contenir des nuances, exceptions, dates ou valeurs \
précises que les relations structurées ci-dessus ne capturent pas forcément intégralement :
{excerpts}

Réponds à la question de l'utilisateur UNIQUEMENT à partir des relations et extraits ci-dessus, \
tu peux citer les passage des chunks pour appuyer le propos. \
Pour chaque affirmation de ta réponse, cite entre parenthèses le document source correspondant \
(le nom donné après "source :" dans la relation utilisée, ou le nom de document indiqué avant \
l'extrait de texte) — ça doit permettre de vérifier chaque affirmation dans le document \
d'origine. Si les relations et le texte source ne contiennent pas assez d'information pour \
répondre avec certitude, dis-le explicitement et précise ce qui manquerait plutôt que \
d'inventer une réponse.

### IMPORTANT ###
Donne moi à la fin de ta réponse ton cheminement de raisonnement, en expliquant comment tu as utilisé les relations du graphe, les extraits de texte source et les sources
pour arriver à ta conclusion.
Si tu as dû faire des hypothèses, indique-les clairement.
"""

    async def answer(self, context: str, chunk_results: list[dict], question: str) -> str:
        system_prompt = self._build_prompt(context, chunk_results)
        return await text_completion_async(self.client, self.model, system_prompt, question)


async def run(
    theme: str, dotenv: str, question: str = QUESTION, *,
    seed_top_k: int = 8, hop_depth: int = 1, max_total_triples: int = 200,
) -> None:
    load_dotenv(dotenv)
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()

    source = source_tag(theme)

    # cfg/embedder construits AVANT la récupération du contexte : contrairement à l'ancien
    # GraphContextRetriever (pur Cypher), VectorGraphContextRetriever a besoin d'embedder la
    # question pour la recherche vectorielle.
    cfg = build_vlm_config(Path(dotenv))
    embedder = EmbedderFactory(cfg).build()

    retriever = VectorGraphContextRetriever(
        driver, embedder, source, seed_top_k=seed_top_k, hop_depth=hop_depth,
        max_total_triples=max_total_triples,
    )
    try:
        context = retriever.as_context(question)
    except neo4j.exceptions.ClientError as exc:
        print(f"(contexte indisponible : {exc}. "
              f"L'index « {CHUNK_VECTOR_INDEX_NAME} » existe-t-il pour ce thème ? "
              "Il est créé par build_kg_from_concepts.py — sans --no-embeddings. Abandon.")
        driver.close()
        return
    print(f"=== Contexte retrouvé (test_source={source!r}) ===")
    print(context)

    citation_resolver = SourceCitationResolver(driver, source)
    all_paths = citation_resolver.resolve_all()
    used_docs = retriever.source_documents()
    print(f"\n=== Documents source utilisés ({len(used_docs)}) ===")
    for doc in used_docs:
        path = all_paths.get(doc)
        print(f"  - {doc} -> {path or '(chemin introuvable — TextSource non créé pour ce doc)'}")

    validator = ChunkVectorValidator(driver, embedder, source)
    chunk_results: list[dict] = []
    print(f"\n=== Top-{validator.top_k} chunks (retrieval vectoriel — envoyés au VLM en complément des triplets) ===")
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
    answer = await answerer.answer(context, chunk_results, question)
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
    ap.add_argument("--seed-top-k", type=int, default=8,
                     help="Nombre de chunks utilisés comme points d'ancrage pour l'expansion (défaut : 8).")
    ap.add_argument("--hop-depth", type=int, default=1,
                     help="Profondeur d'expansion Cypher depuis chaque entité ancrée (défaut : 1). "
                          "2+ est non testé à grande échelle (risque d'explosion combinatoire sur les nœuds hubs).")
    ap.add_argument("--max-triples", type=int, default=200,
                     help="Plafond final sur le nombre de triplets dédoublonnés envoyés au LLM, "
                          "triés par pertinence (score du chunk-ancre) avant troncature (défaut : 200).")
    args = ap.parse_args()
    asyncio.run(run(
        args.theme, args.dotenv, args.question,
        seed_top_k=args.seed_top_k, hop_depth=args.hop_depth, max_total_triples=args.max_triples,
    ))


if __name__ == "__main__":
    main()
