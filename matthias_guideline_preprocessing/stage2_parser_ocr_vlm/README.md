# Étape 2 – Pipeline de parsing, structuration et enrichissement VLM

Cette étape vise à **parser, structurer et enrichir le contenu extrait des PDF** en s’appuyant sur la technologie avancée de la suite Docling, notamment ses modules d’extraction de tables, de description d’images et d’analyse Vision-Language Model (VLM).  
L’ensemble des scripts de cette étape permet d’obtenir des exports exploitables pour l’analyse, la recherche, l’indexation ou l’enrichissement sémantique.

## Fonctionnement général

### 1. Chargement de la configuration et des modèles
- Chargement des variables d’environnement (API, modèles VLM, certificats, chemins).
- Définition des prompts pour guider la description d’images et l’extraction de tables (instructions précises pour le VLM).

### 2. Initialisation de la pipeline Docling
- Utilisation de la classe `DocumentConverter` et de la pipeline `VlmPipeline` de Docling.
- Activation des modules :
  - Extraction avancée des textes, titres, listes, tableaux, images, liens.
  - Description automatique des images techniques (diagrammes, schémas, tableaux, figures…).
  - Extraction exhaustive des tables, y compris celles encodées en markdown ou LaTeX.

### 3. Traitement du document PDF
- Le PDF est chargé depuis le dossier d’entrée.
- Docling analyse chaque page et segmente le contenu en :
  - Structure logique (titres, paragraphes, listes…)
  - Tables (détection, structure, export CSV/HTML)
  - Images (description technique via VLM, avec ou sans contexte)
  - Liens et annotations
- Les images sont extraites et décrites automatiquement par le modèle VLM, qui peut être guidé par le contexte textuel environnant.

### 4. Exports multi-formats
- **CSV** :  
  - Export markdown brut et export structuré des tables détectées.
- **HTML** :  
  - Export HTML pour chaque table détectée (pour une visualisation fidèle).
- **Markdown** :  
  - Export du document complet, structuré et enrichi.
- **JSON/JSONL** :  
  - Export structuré des tables ou des résultats d’analyse.
- **Console** :  
  - Affichage du markdown extrait pour vérification rapide.

### 5. Gestion des erreurs et logs
- Vérification de la présence des fichiers d’entrée.
- Création automatique des dossiers de sortie.
- Logs détaillés pour chaque étape (nombre de tables détectées, chemins des fichiers exportés, temps d’exécution…).

---

## Technologies et modules utilisés

- **Docling** :  
  - `VlmPipeline` : pipeline d’analyse avancée basée sur les modèles Vision-Language.
  - `DocumentConverter` : orchestration du parsing, de l’extraction et de l’export.
  - Extraction de tables, images, textes, listes, titres, liens, annotations.
  - Export multi-format (CSV, HTML, Markdown, JSON).

- **Vision-Language Model (VLM)** :  
  - Modèle de type multimodal (texte + image) pour l’extraction sémantique et la description automatique des images techniques.
  - Possibilité de guider la description par le contexte textuel environnant.

- **Pandas** :  
  - Structuration et export des données tabulaires (CSV, JSONL).

- **Python standard** :  
  - Gestion des chemins (`pathlib`)
  - Chargement des variables d’environnement (`dotenv`)
  - Logging et gestion des erreurs

---

## Points clés

- **Pipeline entièrement automatisée** : un seul script permet de passer du PDF brut à des exports structurés et enrichis, sans intervention manuelle.
- **Extraction exhaustive** : aucune information technique (table, schéma, diagramme, liste, titre…) n’est ignorée.
- **Description d’images avancée** : les images sont non seulement extraites mais aussi décrites automatiquement, ce qui enrichit considérablement le markdown final.
- **Modularité** : prompts et options facilement adaptables selon le type de document ou le modèle VLM utilisé.
- **Interopérabilité** : les exports produits sont prêts pour l’étape suivante (enrichissement, indexation, extraction de liens…).

---

## Utilisation

1. Placez votre PDF dans le dossier d’entrée prévu (`data/input_files/`).
2. Lancez le script principal de la pipeline.
3. Retrouvez les fichiers exportés dans le dossier de sortie (`data/output_files/stage2_test/` et sous-dossier `tables/`).

---

## Remarques

- Cette étape exploite la puissance de Docling et des modèles Vision-Language pour obtenir une structuration et un enrichissement sémantique avancés.
- Les fichiers produits (markdown enrichi, tables CSV/HTML, descriptions d’images) sont directement exploitables pour l’analyse documentaire, la recherche ou l’indexation.
- L’approche est modulaire : chaque composant (extraction de tables, description d’images, parsing) peut être adapté ou remplacé selon les besoins du projet.

---

https://github.com/docling-project/docling/discussions/354