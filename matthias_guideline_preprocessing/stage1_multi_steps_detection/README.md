# Étape 1 – Pipeline de détection multi-étapes

Cette étape est dédiée à la **détection et à l’extraction du contenu structuré** des documents PDF grâce à une approche multi-étapes. L’objectif est de préparer les données pour les traitements ultérieurs : OCR, extraction de tableaux, enrichissement sémantique, etc.

## Scripts principaux de cette étape

### 1. Visualisation des zones détectées
Un script permet de générer une image annotée à partir d’un document PDF :  
- Il convertit chaque page du document en image.
- Il lit le fichier de balises structurées généré par l’OCR (format doctags).
- Il dessine des rectangles colorés autour de chaque zone détectée (texte, image, tableau, etc.), chaque type ayant sa propre couleur.
- Il sauvegarde ou affiche l’image annotée pour un contrôle qualité visuel rapide.

### 2. Pipeline d’extraction multi-format
Un pipeline automatisé permet d’extraire le contenu du document en plusieurs formats :
- Il prend en entrée un document PDF.
- Il applique la détection OCR et structurelle (textes, tableaux, images, liens).
- Il exporte le résultat dans plusieurs formats : balises structurées (doctags), JSON, markdown, texte brut.
- Les fichiers générés sont organisés dans une arborescence de sortie pour faciliter les traitements ultérieurs.

### 3. Repositionnement des balises structurées
Un script de post-traitement permet de garantir que les balises structurées sont dans le bon ordre :
- Il lit le fichier de balises structurées généré précédemment.
- Il analyse la position verticale (y0) de chaque balise pour chaque page.
- Il trie les balises par ordre d’apparition dans le document, page par page, pour refléter fidèlement la structure du PDF d’origine.
- Il réécrit le fichier de balises structurées corrigé, prêt pour la conversion en markdown ou d’autres usages.

---

## Objectifs principaux

   - Détecter et extraire tous les blocs de contenu pertinents (textes, tableaux, images, liens) à partir des PDF.
   - Générer des fichiers intermédiaires dans différents formats pour faciliter les étapes suivantes.
   - /!\ ATTENTION, le contenu descriptif des images n'est pas extrait avec ce processus. Les images sont détectées (balise "picture"), mais leur description nécessite une étape ultérieure dédiée à l’analyse d’image.

## Étapes de la pipeline

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
   - `*.doctags` : Balises structurées avec coordonnées et types de contenu.
   - `*.json` : Export structuré JSON.
   - `*.md` :  Export Markdown de la structure du document.
   - `*.txt` : Export texte brut.

## Utilisation typique

1. Placer votre document dans le dossier d'entrée configuré.
2. Lancer le pipeline de détection multi-étapes.
3. Retrouver les fichiers exportés dans le dossier de sortie configuré.

## Remarques
   - Cette étape est conçue pour être modulaire et extensible.
   - Vous pouvez adapter la pipeline pour utiliser différents moteurs OCR ou stratégies de détection selon vos besoins.
   - Les fichiers produits sont destinés à être utilisés en complément de l’étape suivante (parsing, OCR, VLM, etc.).

## Outil de contrôle qualité visuel

Un script complémentaire permet de vérifier visuellement la qualité de l’extraction des zones détectées :
- Il charge le document et le convertit en image.
- Il lit le fichier de balises structurées pour récupérer les coordonnées et types de toutes les zones détectées.
- Il dessine un rectangle coloré autour de chaque zone sur l’image, avec une couleur différente selon le type de contenu.
- Il sauvegarde ou affiche l’image annotée, ce qui permet de vérifier d’un coup d’œil si les zones détectées correspondent bien aux éléments réels du document.

**En résumé :**
- Cette étape fournit tous les outils nécessaires pour extraire, structurer et contrôler le contenu d’un document avant enrichissement ou conversion avancée.