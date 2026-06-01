# Étape 1 – Pipeline de détection multi-étapes

Cette étape est dédiée à la **détection et à l’extraction du contenu structuré** des documents PDF grâce à une approche multi-étapes. L’objectif est de préparer les données pour les traitements ultérieurs : OCR, extraction de tableaux, enrichissement sémantique, etc. </br>
Cette étape la pipeline dolcing pour extraire une première fois les informations des documents selon ce modèle:</br>
https://docling-project.github.io/docling/_generated/examples/custom_convert/


## Scripts principaux

- 1) **pipeline_multietape.py**  
  Lance la détection multi-étapes et exporte le contenu du PDF dans plusieurs formats : balises structurées (doctags), JSON, markdown, texte brut. Les fichiers générés sont organisés dans une arborescence de sortie.

- 2) **control_doctags_balise_loc_y0.py**  
  Trie et repositionne les balises structurées pour garantir la cohérence avec le PDF d’origine :  
  - Analyse la position verticale (y0) de chaque balise pour chaque page.
  - Trie les balises par ordre d’apparition dans le document, page par page.
  - Réécrit le fichier de balises structurées corrigé.

  - 3) **opencv_checker.py**  
  Génère une image annotée à partir d’un PDF : chaque zone détectée (texte, image, tableau, etc.) est entourée d’un rectangle coloré selon son type pour un contrôle qualité visuel rapide.

## Pipeline de traitement

1. **Chargement du document**
   - Préparation du fichier pour le traitement (normalisation du chemin, validation).

2. **Détection du contenu**
   - Utilisation d’un moteur OCR et d’analyse de structure pour détecter :
      - Les blocs de texte (paragraphes, titres, listes)
      - Les tableaux (avec leur structure)
      - Les images et figures (détection uniquement)
      - Les liens hypertextes et annotations

3. **Export multi-format**
   - Export du contenu détecté dans plusieurs formats :
      - **Balises structurées (doctags)** : pour le post-traitement avancé et le matching de zones/boîtes.
      - **JSON** : pour des données structurées, exploitables par des scripts.
      - **Markdown** : pour une structure lisible par l’humain et une relecture rapide.
      - **Texte brut** : pour une analyse textuelle simple.

4. **Préparation pour l’étape suivante**
   - Organisation des sorties dans une arborescence cohérente.
   - Vérification que tous les éléments détectés sont prêts pour le parsing, l’OCR ou l’enrichissement ultérieur.

## Fichiers générés

- Balises structurées avec coordonnées et types de contenu : `*.doctags`
- Balises structurées avec coodonnées et types de contenu réarranger pour position y0 : `*_reordered.doctags`
- Export structuré JSON : `*.json`   
- Export Markdown de la structure du document : `*.md`   
- Export texte brut : `*.txt`

## Utilisation typique

1. Placez votre document dans le dossier d'entrée configuré.
2. Lancez le pipeline de détection multi-étapes avec `1_pipeline_multietape_v2.py`.
3. Si besoin, corrigez l’ordre des balises avec `2_control_doctags_balise_loc_y0.py`.
4. Vérifiez la qualité de la détection avec `3_opencv_checker.py`.
5. Retrouvez les fichiers exportés dans le dossier de sortie configuré.

## Remarques

- Cette étape est conçue pour être **modulaire et extensible**.
- Vous pouvez adapter la pipeline pour utiliser différents moteurs OCR ou stratégies de détection selon vos besoins.
- Les fichiers produits sont destinés à être utilisés en complément de l’étape suivante (parsing, OCR, VLM, etc.).
- Le contenu descriptif des images n'est pas extrait à cette étape : les images sont détectées (balise `<picture>`), mais leur description nécessite une étape ultérieure dédiée à l’analyse d’image.

**En résumé :**
Cette étape fournit tous les outils nécessaires pour extraire, structurer et contrôler le contenu d’un document avant enrichissement ou conversion avancée.