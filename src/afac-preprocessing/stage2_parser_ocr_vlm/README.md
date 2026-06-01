# Étape 2 – Pipeline de parsing, structuration et enrichissement VLM

Cette étape vise à **parser, structurer et enrichir le contenu extrait des PDF** grâce à la suite Docling et à l’intégration de modèles Vision-Language (VLM).  
Elle permet d’obtenir des exports exploitables pour l’analyse, la recherche, l’indexation ou l’enrichissement sémantique.

## Scripts principaux

- **Scripts principaux** :  
- 1) **export_table_docling** : extrait les tables selon le pipeline docling et export les table au format CSV et HTML
- 2) **csv_to_json** : convertie les tables CSV en jsonline
- 3) **load_jsonline_docling** : charge le jsonline dans le doctags
- 4) **description_image_context** : ne parse que les images détectées au VLM pour les décrire selon le prompt et charge les description àa la place des balises <picture> pour ne garder que du texte. Créé également un fichier markdown .md avec seulement la description des images avec leurs coordonnées sur le PDF pour la traçabilité.

## Fonctionnement général

1. **Chargement de la configuration**
   - Chargement des variables d’environnement (API, modèles VLM, certificats, chemins).
   - Définition des prompts pour guider la description d’images et l’extraction de tables.

2. **Initialisation de la pipeline Docling**
   - Utilisation de `DocumentConverter` et de la pipeline `VlmPipeline`.
   - Activation des modules : extraction avancée des textes, titres, listes, tableaux, images, liens.

3. **Traitement du document PDF**
   - Analyse page par page : segmentation logique, extraction de tables, description automatique des images techniques (diagrammes, schémas, figures…).
   - Les images sont extraites et décrites automatiquement par le modèle VLM, guidé par le contexte textuel.

4. **Exports multi-formats**
   - **CSV** : export markdown brut et structuré des tables détectées.
   - **HTML** : export HTML pour chaque table détectée.
   - **Markdown** : export du document complet, structuré et enrichi.
   - **JSON/JSONL** : export structuré des tables ou des résultats d’analyse.
   - **Console** : affichage du markdown extrait pour vérification rapide.

5. **Gestion des erreurs et logs**
   - Vérification de la présence des fichiers d’entrée.
   - Création automatique des dossiers de sortie.
   - Logs détaillés pour chaque étape (nombre de tables détectées, chemins des fichiers exportés, temps d’exécution…).

## Technologies et modules utilisés

- **Docling** :  
  - `VlmPipeline` : pipeline d’analyse avancée basée sur les modèles Vision-Language.
  - `DocumentConverter` : orchestration du parsing, de l’extraction et de l’export.
  - Extraction de tables, images, textes, listes, titres, liens, annotations.
  - Export multi-format (CSV, HTML, Markdown, JSON).

- **Vision-Language Model (VLM)** :  
  - Modèle multimodal (texte + image) pour l’extraction sémantique et la description automatique des images techniques.
  - Possibilité de guider la description par le contexte textuel environnant.

- **Pandas** :  
  - Structuration et export des données tabulaires (CSV, JSONL).

- **Python standard** :  
  - Gestion des chemins (`pathlib`)
  - Chargement des variables d’environnement (`dotenv`)
  - Logging et gestion des erreurs

## Fichiers générés

- Export markdown enrichi du document complet : `*.md`
- Tables extraites au format CSV : `tables/*.csv`
- Tables extraites au format HTML : `tables/*.html`
- Export structuré des tables ou résultats d’analyse : `*.json` ou `*.jsonl`

## Utilisation

1. Placez votre PDF dans le dossier d’entrée (`data/input_files/`).
2. Lancez le script principal de la pipeline.
3. Retrouvez les fichiers exportés dans le dossier de sortie (`data/output_files/stage2_test/` et sous-dossier `tables/`).

## Points clés

- **Pipeline entièrement automatisée** : du PDF brut aux exports structurés et enrichis, sans intervention manuelle.
- **Extraction exhaustive** : aucune information technique (table, schéma, diagramme, liste, titre…) n’est ignorée.
- **Description d’images avancée** : les images sont extraites et décrites automatiquement, enrichissant le markdown final.
- **Modularité** : prompts et options facilement adaptables selon le type de document ou le modèle VLM utilisé.
- **Interopérabilité** : les exports produits sont prêts pour l’étape suivante (enrichissement, indexation, extraction de liens…).

## Remarques

- Cette étape exploite la puissance de Docling et des modèles Vision-Language pour obtenir une structuration et un enrichissement sémantique avancés.
- Les fichiers produits (markdown enrichi, tables CSV/HTML, descriptions d’images) sont directement exploitables pour l’analyse documentaire, la recherche ou l’indexation.
- L’approche est modulaire : chaque composant (extraction de tables, description d’images, parsing) peut être adapté ou remplacé selon les besoins du projet.

[Docling – Discussions et documentation](https://github.com/docling-project/docling/discussions/354)