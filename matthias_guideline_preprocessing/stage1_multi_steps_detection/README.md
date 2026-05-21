# Étape 1 – Pipeline de détection multi-étapes

Cette étape est dédiée à la **détection et à l’extraction du contenu structuré** des documents PDF grâce à une approche multi-étapes. L’objectif est de préparer les données pour les traitements ultérieurs : OCR, extraction de tableaux, enrichissement sémantique, etc.
Il y a 4 scripts dans cette étape:
- **OpenCV_checker_single.py** : Créé des boxes autour de tous les éléments détectés par l'OCR docling pour vérifier s'il y a des erreurs de détection (première page du PDF)
- **OpenCV_checker.py** : Créé des boxes autour de tous les éléments détectés par l'OCR docling pour vérifier s'il y a des erreurs de détection (toutes les page du PDF)
- **pipeline_multietape.py** : Pipeline de docling qui extrait dans différents format l'extraction EasyOCR, ici en Json, markdown, text et doctags.
- **control_doctags_balise_loc_y0.py** : Ce script, regarde la position y0 des balises dans le doctags et repositionne les balises si necessaire dans le bon ordre d'apparition des éléments (necessaire pour avoir une structure fidèle au PDF initial, pour le fichier markdown final).

## Objectifs principaux

   - Détecter et extraire tous les blocs de contenu pertinents (textes, tableaux, images, liens) à partir des PDF.
   - Générer des fichiers intermédiaires dans différents formats pour faciliter les étapes suivantes.
   - /!\ ATTENTION, le contenu descriptif des images n'est pas extrait avec ce processus. Docling EasyOCR permet de déecter les images (balise "picture" dans le .doctags), cependant, nous n'en n'extrayons pas le contenu. Pour cela, il faut utiliser l'étape 2 avec la pipeline VLM de docling et les prompts pour décrire l'image. De là, on peut retrouver l'image décrite et repositionner le contenu dans la bonne balise avec le processus de matching des boxs.

## Étapes de la pipeline

1. **Chargement du PDF**
   - Charger le document PDF en entrée.
   - Préparer le fichier pour le traitement (normalisation du chemin, validation).

2. **Détection du contenu**
   - Utiliser Docling avec EasyOCR :
      - Les blocs de texte (paragraphes, titres, listes)
      - Les tableaux (avec leur structure)
      - Les images et figures /!\ EasyOCR détecte la présence de l'image (balise "picture"), mais ne la décrit pas.
      - Les liens hypertextes et annotations /!\ un autre script permet d'extraire les liens URL et de les associer au texte correspondant.

3. **Export multi-format**
   - Exporter le contenu détecté dans plusieurs formats :
      - **Doctags** : pour le post-traitement avancé et le matching de zones/boîtes.
      - **JSON** : pour des données structurées, exploitables par des scripts.
      - **Markdown** : pour une structure lisible par l’humain et une relecture rapide.
      - **Texte brut** : pour une analyse textuelle simple.
 
4. **Préparation pour l’étape 2**
   - Organiser les sorties dans une arborescence cohérente.
   - S’assurer que tous les éléments détectés sont prêts pour le parsing, l’OCR ou l’enrichissement ultérieur.

## Fichiers générés
   - `*.doctags` : Tags Docling avec coordonnées et types de contenu.
   - `*.json` : Export structuré JSON.
   - `*.md` :  Export Markdown de la structure du document.
   - `*.txt` : Export texte brut.


## Utilisation typique

1. Placer votre PDF dans le dossier dans le dossier d'entrée configuré.
2. Lancer le script de pipeline de l’étape 1.
3. Retrouver les fichiers exportés dans le dossier de sortie configuré.

## Remarques
   - Cette étape est conçue pour être modulaire et extensible.
   - Vous pouvez adapter la pipeline pour utiliser différents moteurs OCR ou stratégies de détection selon vos besoins.
   - Les fichiers produits sont destinés à être utilisés en complément de l’étape 2 (parsing, OCR, VLM, etc.).

## Additionnalement à ce projet:

le fichier **OpenCV_test_checker.py** permet de vérifier visuellement la qualité de l’extraction des zones détectées par Docling/EasyOCR.
Le script fonctionne ainsi :
- Il charge le PDF et le convertit en image (une image par page, généralement à 300 dpi).
- Il lit le fichier `.doctags` pour récupérer les coordonnées et types de toutes les boxes détectées (textes, images, tableaux, etc.).
- Il dénormalise les coordonnées pour les adapter à la taille de l’image générée.
- Il dessine un rectangle coloré autour de chaque box sur l’image, avec une couleur différente selon le type de contenu (texte, image, table, etc.).
- Il sauvegarde (et/ou affiche) l’image annotée, ce qui permet de vérifier d’un coup d’œil si les zones détectées correspondent bien aux éléments réels du document.

**En résumer :**
1. Ce script est donc un outil de contrôle qualité visuel, très utile pour valider ou ajuster les paramètres d’extraction.
2. Le même script fonctionne pour tester une seule :
**OpenCV_test_checker_single.py**