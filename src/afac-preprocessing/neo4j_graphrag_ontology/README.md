# Neo4j GraphRAG — Knowledge Graph AFAC

Construction d'un **knowledge graph** à partir des documents AFAC déjà prétraités par le
pipeline (`data/output_files_preprocessing/`), pour tester si un retrieval *guidé par graphe*
aide le LLM/VLM à trouver la bonne source, en complément du retrieval par embeddings.

Pour construire le graphe et l'interroger, voir **[`extraction_concepts/README.md`](extraction_concepts/README.md)**
— ce fichier-ci ne couvre que l'installation de Neo4j.

## Installer Neo4j sur WSL (Docker)

L'environnement WSL2 dispose déjà d'un daemon Docker natif (systemd activé, Ubuntu 24.04) :
pas besoin de Docker Desktop, de paquets apt, ni de Neo4j Desktop.

```bash
mkdir -p ~/neo4j-afac/{data,logs,import,plugins}

docker run -d --name neo4j-afac --restart unless-stopped \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/<mot-de-passe-local> \
  -e NEO4J_PLUGINS='["apoc"]' \
  -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
  -v ~/neo4j-afac/data:/data \
  -v ~/neo4j-afac/logs:/logs \
  -v ~/neo4j-afac/import:/import \
  neo4j:5.26
```

- **Interface Browser** : http://localhost:7474 (login `neo4j` / le mot de passe choisi ci-dessus,
  aussi présent dans `NEO4J_PASSWORD` côté `.env.test`, gitignoré) — accessible aussi depuis
  Windows, WSL2 forwarde `localhost`.
- **Bolt (depuis Python)** : `bolt://localhost:7687`
- Vérifier le démarrage : `docker logs -f neo4j-afac` jusqu'au message *"Started."*

> La commande `docker run` ci-dessus ne sert **qu'une seule fois** (création du conteneur).
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
relancer le `docker run` (le graphe persiste dans `~/neo4j-afac/data`, à supprimer aussi
pour un vrai reset).

## Configuration

- Ne pas committer le mot de passe. `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` sont dans
  `.env.test` (déjà chargé via `python-dotenv` par tous les scripts du projet).
- Le VLM est un **endpoint interne compatible OpenAI** (`VLM_URL` + CA système), pas OpenAI
  cloud — tous les scripts du pipeline pointent déjà vers cet endpoint via `utils/vlm_client.py`.

## Visualisation — taille des nœuds selon leur connectivité

Neo4j Browser n'a pas de notion de « degré » calculée à la volée pour le style : il faut
d'abord stocker le degré comme propriété, puis mapper cette propriété à la taille dans le
panneau de style. À refaire après chaque rechargement du graphe, le degré n'étant pas mis à
jour automatiquement.

1. Dans la barre de requête du Browser, calculer et stocker le degré de chaque nœud :

   ```cypher
   MATCH (n)
   SET n.degree = size((n)--())
   ```

2. Afficher le graphe (`MATCH (n)-[r]->(m) RETURN n, r, m`, ou une requête filtrée par
   `test_source` — voir `extraction_concepts/README.md`).
3. Dans le panneau latéral (légende par label), cliquer sur un label puis choisir **Size
   mapped by...** → sélectionner la propriété `degree`. Les nœuds les plus connectés
   apparaissent alors visiblement plus gros que les nœuds périphériques.
