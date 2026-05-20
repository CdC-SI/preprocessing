# Étape 2 – Pipeline de parsing et VLM (`pipeline_test1.py`)

Cette étape vise à **parser, structurer et enrichir le contenu extrait des PDF** en combinant les méthodes avancées de la suite Docling : extraction de tables, description d’images et modèles Vision-Language (VLM).  
Le script `pipeline_test1.py` automatise l’ensemble du processus pour produire des exports exploitables pour l’analyse, la recherche ou l’indexation.
Il y a 6 scripts dans cette étape:
- **pipeline_base.py** : pipeline utilisée initialement pour guider le VLM, pas de description d'image. /!\ Le script n'est pas utilisé /!\
- **pipeline_test1.py** : pipeline améliorée par rapport à **pipeline_base.py**, permet de reconstruire les tables si la détection est mauvaise.
- **stage2_csv_json.py** : script de conversion des tables d'étectées csv par **tage2_export_table_docling.py** en **Jsonline**
- **stage2_export_table_docling.py** : pipeline modifiée de docling pour extraire les tables détectées aux formats csv et json. </br>https://docling-project.github.io/docling/examples/export_tables/
- **test_image_description_plus_context.py** : Utilise le module docling VLM pour décrire les images extraires de nos PDF avec un prompt et en ajoutant le contexte autour de l'image pour guider l'analyse.</br>https://docling-project.github.io/docling/examples/pictures_description/
- **test_image_description.py** : Utilise le module docling VLM pour décrire les images extraires de nos PDF avec un prompt.</br>https://docling-project.github.io/docling/examples/pictures_description/

## Fonctionnement général

1. **Chargement de la configuration et des modèles**
   - Chargement des variables d’environnement (API, modèle VLM, certificats).
   - Configuration des prompts pour la description d’images et l’extraction de tables (instructions précises pour le VLM).

2. **Initialisation de la pipeline Docling**
   - Utilisation de la classe `DocumentConverter` avec la pipeline `VlmPipeline` de Docling.
   - Activation de :
     - L’extraction VLM des textes, titres, listes, tableaux, images.
     - La description automatique des images techniques (diagrammes, schémas, tableaux…).
     - L’extraction exhaustive des tables, y compris celles en markdown ou LaTeX.

3. **Traitement du document PDF**
   - Le PDF est chargé depuis le dossier d’entrée.
   - Docling analyse chaque page :  
     - Structure logique (titres, paragraphes, listes…)
     - Tables (détection, structure, export CSV/HTML)
     - Images (description technique via VLM)
     - Liens et annotations

4. **Exports multi-formats**
   - **CSV** :  
     - Export markdown brut (`af_test1.csv`)
     - Export structuré complet (`*_structured.csv`)
   - **CSV/HTML** pour chaque table détectée (dossier `tables/`)
   - **Markdown** :  
     - Export du document complet
   - **Console** :  
     - Affichage du markdown extrait pour vérification rapide

5. **Gestion des erreurs et logs**
   - Vérification de la présence des fichiers d’entrée.
   - Création automatique des dossiers de sortie.
   - Logs détaillés pour chaque étape (nombre de tables détectées, chemins des fichiers exportés, temps d’exécution…).

---

## Méthodes et modules utilisés

- **Docling** :  
  - `VlmPipeline` pour l’extraction avancée (VLM)
  - `DocumentConverter` pour orchestrer le traitement
  - Extraction de tables, images, textes, listes, titres, liens
  - Export multi-format (CSV, HTML, Markdown)

- **Vision-Language Model (VLM)** :  
  - Extraction sémantique et structurée du contenu
  - Description automatique des images techniques

- **Pandas** :  
  - Structuration et export des données tabulaires

- **Python standard** :  
  - Gestion des chemins (`pathlib`)
  - Chargement des variables d’environnement (`dotenv`)
  - Logging et gestion des erreurs

---

## Points clés

- **Pipeline entièrement automatisée** : un seul script pour passer du PDF brut à des exports structurés et enrichis.
- **Extraction exhaustive** : aucune information technique (table, schéma, diagramme, liste, titre…) n’est ignorée.
- **Modularité** : prompts et options facilement adaptables selon le type de document ou le modèle VLM utilisé.
- **Interopérabilité** : les exports produits sont prêts pour l’étape suivante (enrichissement, indexation, extraction de liens…).

---

## Utilisation

1. Placez votre PDF dans `data/input_files/`.
2. Lancez le script `pipeline_test1.py`.
3. Retrouvez les fichiers exportés dans `data/output_files/stage2_test/` et dans le sous-dossier `tables/`.

---