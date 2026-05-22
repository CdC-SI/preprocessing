# Étape 3 – Extraction et association des liens URL

Cette étape a pour objectif d’**extraire tous les liens hypertextes (URL) externes présents dans le document PDF** et de préparer leur association avec les zones de texte (ou "boxes") détectées lors des étapes précédentes (stage 1 et 2).  
Cela permet, par la suite, de relier chaque URL à la portion de texte ou de structure correspondante dans le document enrichi.

Il y a 2 scripts principaux dans ce sous-dossier :

---

## 1. `get_url.py` — Extraction des liens externes

- **Chargement du PDF**
  - Le script ouvre le fichier PDF cible à l’aide de la bibliothèque PyMuPDF (`fitz`).

- **Extraction des liens**
  - Pour chaque page du PDF, il détecte toutes les annotations de type lien.
  - **Seuls les liens externes** sont extraits : le script filtre pour ne garder que les liens dont l’URL commence par `http://`, `https://` ou `mailto:`.
  - Pour chaque lien trouvé, il extrait :
    - Le numéro de page
    - Le texte contenu dans la zone du lien (si disponible)
    - L’URL (si c’est un lien externe)
    - Le type de lien (toujours "URI" ici)
    - Les détails techniques du lien (dont la position de la box, convertie pour être sérialisable)

- **Affichage et sauvegarde**
  - Les informations extraites sont affichées dans la console pour vérification rapide.
  - Tous les liens sont sauvegardés dans un fichier au format JSONLines (`.jsonl`), chaque ligne correspondant à un lien détecté.

---

## 2. `matchURL.py` — Association et enrichissement des liens

- **Chargement des données**
  - Le script charge les liens extraits (`.jsonl`) et le fichier `.doctags` issu de Docling ou d’une étape précédente.

- **Matching des liens avec les zones de texte**
  - Pour chaque lien, il recherche la box ou le bloc de texte du `.doctags` le plus pertinent, en utilisant :
    - Le chevauchement des coordonnées (overlap entre la box du lien et les boxes du `.doctags`)
    - La similarité du texte (si disponible)
  - Si plusieurs liens sont présents dans le même bloc, ils sont tous associés correctement.

- **Injection des liens au format Markdown**
  - Le texte du lien est inséré dans le texte du bloc correspondant, au format `[texte du lien](URL)` (Markdown).
  - Si le texte du lien est trouvé dans le bloc, il est remplacé par la version Markdown ; sinon, le lien est ajouté à la fin du bloc.
  - Le document `.doctags` enrichi est sauvegardé pour une utilisation ultérieure.

---

## Ce qui est extrait et pourquoi

- **URL et métadonnées** :  
  Permet de recenser tous les liens cliquables du document, essentiels pour la navigation, l’indexation ou l’enrichissement sémantique.
- **Position (box)** :  
  La position de chaque lien (rectangle sur la page) est extraite pour permettre, lors d’une étape ultérieure, de faire le rapprochement ("matching") avec les zones de texte ou d’éléments structurés détectés lors des stages 1 et 2.
  - Les boxes sont définies comme suit : `<x0><y0><x1><y1>`
- **Texte associé** :  
  Le texte contenu dans la zone du lien est extrait pour faciliter le matching sémantique ou fuzzy avec les blocs de texte OCRisés ou structurés.

---

## Pourquoi cette étape ?

- **Association fine** :  
  En extrayant à la fois l’URL et sa position, puis en les associant précisément aux blocs de texte, on enrichit le document pour la navigation, la recherche ou l’indexation.
- **Enrichissement documentaire** :  
  Chaque URL est replacée dans son contexte textuel, au bon endroit du document, au format Markdown, facilitant ainsi la génération de documents enrichis ou de jeux de données pour l’IA.
- **Préparation à l’étape suivante** :  
  Le `.doctags` enrichi peut être utilisé pour générer un JSONL final, ou pour d’autres traitements automatiques.

---

## Résumé

- **Extraction exhaustive des liens externes** (URL, position, texte, type) avec `get_url.py`
- **Matching et enrichissement** des blocs de texte avec `matchURL.py`, insertion des liens au format Markdown
- **Sauvegarde structurée** au format JSONLines et `.doctags` enrichi pour traitement ultérieur
- **But final** : relier chaque URL externe à la bonne zone du document pour enrichir la navigation et la recherche d’information

---