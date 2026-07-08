# Neo4j GraphRAG — Knowledge Graph AFAC

Construction d'un **knowledge graph** à partir des documents AFAC déjà prétraités par le
pipeline (`data/output_files_preprocessing/`), afin de tester si un retrieval *guidé par
graphe* aide le LLM/VLM à trouver la bonne source, en complément du retrieval par embeddings.

> **Hypothèse à valider** : les entités du domaine (systèmes, codes ARC, statuts, processus)
> réapparaissent **d'un document à l'autre**. Le retrieval par embeddings traite chaque
> document isolément ; un graphe permet de *traverser* d'un document à l'autre via les entités
> partagées (ex. une question « mineur devenant majeur → ARC 31 » relie le doc *Mineur* au doc
> *Confirmer l'adhésion* via les nœuds partagés `ARC 31` / `ARC 61`).

---

## 1. Installer Neo4j sur WSL (Docker — recommandé)

L'environnement WSL2 dispose déjà d'un daemon Docker natif (systemd activé, Ubuntu 24.04) :
pas besoin de Docker Desktop, de paquets apt, ni de Neo4j Desktop.

```bash
mkdir -p ~/neo4j-afac/{data,logs,import,plugins}

docker run -d --name neo4j-afac --restart unless-stopped \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/afac_dev_password \
  -e NEO4J_PLUGINS='["apoc"]' \
  -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
  -v ~/neo4j-afac/data:/data \
  -v ~/neo4j-afac/logs:/logs \
  -v ~/neo4j-afac/import:/import \
  neo4j:5.26
```

Ensuite :

- **Interface Browser** : http://localhost:7474 (login `neo4j` / `afac_dev_password`) —
  accessible aussi depuis Windows, WSL2 forwarde `localhost`.
- **Bolt (depuis Python)** : `bolt://localhost:7687`
- Vérifier le démarrage : `docker logs -f neo4j-afac` jusqu'au message *"Started."*

> ⚠️ La commande `docker run` ci-dessus ne sert **qu'une seule fois** (création du conteneur).
> Relancée ensuite, elle échoue avec *"name already in use"*. Pour l'usage quotidien :

```bash
docker start neo4j-afac      # démarrer le conteneur existant
docker stop  neo4j-afac      # arrêter
docker restart neo4j-afac    # redémarrer
docker logs -f neo4j-afac    # suivre les logs
docker ps --filter name=neo4j-afac   # vérifier qu'il tourne
```

Grâce à `--restart unless-stopped`, le conteneur redémarre aussi automatiquement au boot
(sauf si arrêté manuellement). Pour repartir de zéro : `docker rm -f neo4j-afac` puis
relancer le `docker run` (⚠️ le graphe persiste dans `~/neo4j-afac/data`, à supprimer aussi
pour un vrai reset).

**À faire côté configuration :**

- Ne pas committer le mot de passe. Ajouter `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
  au `.env` existant (déjà chargé via `python-dotenv`), et gitignorer le fichier.
- Le VLM est un **endpoint interne compatible OpenAI** (`VLM_URL` + `VLM_CA_PEM`), pas
  OpenAI cloud. La librairie KG doit pointer vers **cet** endpoint, pas `api.openai.com`.

## 2. Dépendances Python

Librairie officielle Neo4j pour text → entités/relations → graphe, puis retrieval guidé par
graphe. Compatible avec tout endpoint OpenAI-compatible (donc `VLM_URL`).

```bash
# dans le projet afac-preprocessing (gestion via uv)
uv add neo4j neo4j-graphrag
```

Fournit `SimpleKGPipeline` (extraction entités/relations vers Neo4j via LLM) et des
retrievers (`VectorRetriever`, `VectorCypherRetriever`, `Text2CypherRetriever`) branchables
directement dans le harness `retrieval_protocol_evaluation/`.

## 3. Domaine — entités récurrentes

Visibles dans les documents prétraités (`Mineur_final.md`, résumés `Confirmer l'adhésion`…) :

- **Systèmes** : GEDO, TeleZas3, SITAX, ARA
- **Codes / actions** : ARC 31, ARC 61 (carte AVS13, ouverture CI), motif 7/8
- **Concepts / statuts** : mineur, majeur, adhésion, NAVS, date d'effet, R+F
- **Processus** : liens bpanda (ex. « CSC AF - Traiter les demandes d'adhésion »)
- **Thèmes** (les 20 dossiers) : Mineur, Détachement, Globe-trotter, Lacunes d'assurance…

## 4. Plan de mise en œuvre

| Étape | Action | Sortie |
|-------|--------|--------|
| 1 | Installer Neo4j (§1) + `uv add neo4j neo4j-graphrag` | DB en fonctionnement |
| 2 | **Définir l'ontologie d'abord** — ne pas laisser le LLM inventer les labels | `ontology/afac_ontology.py` : labels de nœuds + types de relations autorisés |
| 3 | **Extraire les triplets** depuis les `*_final.md` déjà produits, en réutilisant le client VLM + sortie structurée Pydantic (comme resume/intent/hyq) | `entities.json` par document |
| 4 | **Charger dans Neo4j** en `(:Document)-[:MENTIONS]->(:Concept)` etc., en dédupliquant les entités partagées par nom normalisé | graphe peuplé |
| 5 | **Brancher un retriever graphe** dans le harness existant comme nouvelle « pipeline » à côté de baseline / v3 | métriques comparables |
| 6 | **Évaluer** avec `metrics.py` existant (recall@k, MRR@5, nDCG@5) vs les baselines embeddings | réponse à « est-ce meilleur ? » |

## 5. Décisions de conception clés

1. **Extraction contrainte par l'ontologie, pas ouverte.** Sur un corpus de ~20 documents,
   un LLM sans contrainte produit `GEDO`, `Gedo`, `système GEDO` comme trois nœuds distincts
   et le graphe devient du bruit. Fournir une liste fermée de types de nœuds
   (`Document, Theme, System, Code, Process, Concept, LegalRef, Condition`) et de relations
   (`APPLIES_TO, REQUIRES, TRIGGERS, EXCLUDES, REFERENCES, PART_OF`). C'est le principal levier
   de qualité.

2. **Le graphe augmente le retrieval, il ne le remplace pas.** Le pattern le plus robuste est
   `VectorCypherRetriever` : embedding de la requête → nœuds « graines » → expansion 1–2 sauts
   dans le graphe → renvoi du voisinage enrichi. On conserve donc les embeddings actuels ; le
   graphe ajoute le tissu connectif. Cela permet aussi un A/B équitable dans le harness existant.

## 6. Structure cible du dossier

```
neo4j_graphrag_ontology/
├── README.md            # ce fichier
├── ontology/
│   └── afac_ontology.py # labels de nœuds + types de relations autorisés
└── graphrag/
    ├── extract_triples.py  # *_final.md → entités/relations (client VLM + Pydantic)
    ├── load_graph.py       # triplets → Neo4j (déduplication par nom normalisé)
    └── retrieve.py         # VectorCypherRetriever branché sur le harness d'évaluation
```

## 7. Prochaine action recommandée

Valider la chaîne bout-en-bout sur **un seul document** (ex. *Mineur*) avant de passer aux 20 :
installer Neo4j → définir l'ontologie → extraire les triplets d'un document → charger → vérifier
le graphe dans le Browser. Une fois le squelette fonctionnel, généraliser au corpus complet puis
brancher l'évaluation.

ENGLISH:
Neo4j baseline :

Current chunking
You never pass text_splitter= to SimpleKGPipeline, so it defaults to FixedSizeSplitter() (kg_builder.py:88 — "Defaults to FixedSizeSplitter()"), whose defaults are:

|Parametre|Value|Meaning|
|---|---|---|
|`chunck_size`|4000|4000 *characters* per chunck|
|`chunck_overlap`|200|200 characters|
|`approximate`|**true**|nudges the cut to the nearest word boundary instead of slicing mid-word|

So your chunk overlap is 200 characters (~5% of chunk size).

générer le graphe complet:
MATCH (n)-[r]->(m) RETURN n, r, m

## 8. Visualisation — taille des nœuds selon leur connectivité

Neo4j Browser n'a pas de notion de « degré » calculée à la volée pour le style : il faut
d'abord stocker le degré comme propriété, puis mapper cette propriété à la taille dans le
panneau de style. À refaire après chaque rechargement du graphe (`build_kg.py` /
`batch_build_kg.py`), le degré n'étant pas mis à jour automatiquement.

1. Dans la barre de requête du Browser, calculer et stocker le degré de chaque nœud :

   ```cypher
   MATCH (n)
   SET n.degree = size((n)--())
   ```

2. Relancer `MATCH (n)-[r]->(m) RETURN n, r, m` pour afficher le graphe.
3. Dans le panneau latéral (légende par label, ex. `Concept`, `System`…), cliquer sur un
   label puis choisir **Size mapped by...** (ou l'icône de taille) → sélectionner la
   propriété `degree`. Les nœuds les plus connectés (ex. `mineur`, `GEDO`) apparaissent
   alors visiblement plus gros que les nœuds périphériques.
