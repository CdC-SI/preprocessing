# PROTOCOLE DE TEST :

## **Stage 1** :  
1) script pipeline_multietape_v2.py  
    - {DOC_NAME}.doctags  
    - {DOC_NAME}.json  
    - {DOC_NAME}.md  
    - {DOC_NAME}.txt  
2) script control_doctags_balise_loc_y0.py  
    - {DOC_NAME}_reordered.doctags  
3) script opencv_checker.py  
    - crée les x fichiers .png qui montrent les éléments détectés par Docling avec une box de couleur et un libellé correspondant au type détecté (texte, header, footer, ...)

## **Stage 2** :  
1) script export_table_docling.py  
    - crée les fichiers .csv et .html des tables extraites par la pipeline Docling ; s’il y a plusieurs tables, plusieurs fichiers sont créés (1 fichier = 1 table)
2) script csv_to_json.py  
    - convertit une table précédemment extraite au format .csv en format .jsonl (Jsonline)
3) script load_jsonline_docling.py  
    - charge et remplace dans les balises doctags ("<otsl>") correspondantes la table au format jsonline à l’emplacement adéquat dans le fichier (remplace les balises otsl par des balises text)
4) description_image_context.py  
    - plusieurs cas possibles (3) :
        . Si le document est expressément exclu de la pipeline de description, le VLM ne décrit pas les images, il saute donc cette étape.  
        . Si aucun document n’est spécifié, on regarde si dans l’environnement la variable qui contrôle le booléen true/false de la pipeline est initialisée et, si oui, à quelle valeur.  
        . Si elle n’est pas initialisée (ni true/false), alors on regarde dans un troisième temps :  
        . Si rien n’est spécifié dans les deux cas précédents, la pipeline de description s’exécute quoi qu’il arrive.

## **Stage 3** :  
1) script get_url.py  
    - extrait les URLs des documents grâce à la librairie fitz (PyMuPDF) au format jsonline en suivant la nomenclature markdown : ["texte de l'url"](l'URL)
2) script match_url.py  
    - rattache les URLs extraites précédemment aux bonnes balises doctags en évitant la redondance et les doublons.

## **Stage 4** :  
1) convert_doctags_to_markdown.py  
    - prend le document doctags généré en fin de pipeline avec les tables, les descriptions d’images si disponibles, les URLs, et retourne un fichier markdown équivalent, robuste, pour être envoyé au LLM.

# Analyse des résultats cas par cas:

## Domicilié dans les DOM-TOM, UE.pdf

**Remarques**:
Extraction des Tables: Bon
Extraction des URL: Bon
Extraction générale du texte: Bon /!\ certains mots sont coupés

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
☺ Territoire britannique de l'océan Indien
```
Que l'émoji heureux est bien capté par l'extraction mais que le suivant pas-content:
```text
 Guadeloupe
```
les smiley content sont détectés, pas les pas-contents problème avec la détection de certains caractères spéciaux.
ou encore, cette partie aurait du être traitée comme une table, mais le pipeline docling ne l'a pas détecté:

```text
60710

Cook Island Norfolk Island

60120
```
Perte de cohérence par exemple : **d'outre -mer** les liaisaons entre les termes.
Toutefois il ne semble pas y avoir d'autres erreurs comme des mots coupé en deux ou des mauvais retour à la ligne.

## Étudiant au tarif de l'AO - Adhésion.pdf

**Remarques**:
Extraction des Tables: Moyen 
Extraction des URL: BON
Extraction générale du texte: Bon /!\ certains mots sont coupés

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
{"Domicilié dans l'UE / AELE": "Ces étudiants ont un délai de 6 mois pour déposer leur demande d'adhésion, à compter du début de la formation à l'étranger. La clause des 5 années d'assurance au préalable à compter du début de leur formation, subsiste. Voir RAVS, art. 5g et 5h. Ils peuvent ainsi rester assurés jusqu'au 31.12 de l'année de leurs 30 ans. Voir LAVS art. 1a 3b. Les étudiants ne doivent pas exercer une activité lucrative en parallèle des études • • •", "Hors UE / AELE": "Ces étudiants ont un délai de 6 mois pour déposer leur demande d'adhésion, à compter du début de la formation à l'étranger. Cependant, les étudiants qui s'annoncent après ce délai mais toujours dans l'année qui suit leur départ de la Suisse, peuvent adhérer à l'AVS/AI facultative . Si les étudiants exercent une activité lucrative en parallèle des études ils peuvent adhérer aux condition s de l'AVS/AI facultative."}

·

·

·

·

·
```
Ici l'erreur appararait déjà dans le doctags lors de l'extraction de base avec le pipeline docling et easyOCR (**pipeline_multietape_v2.py**), nous avons ces balises superfluxs:
```text
<text><loc_50><loc_295><loc_54><loc_300>·</text>
<text><loc_50><loc_333><loc_54><loc_339>·</text>
<text><loc_50><loc_372><loc_54><loc_377>·</text>
<text><loc_50><loc_402><loc_54><loc_408>·</text>
<text><loc_50><loc_418><loc_54><loc_423>·</text>
```
il y a également quelques erreurs de détection de mots:
```text
l' étudiant
```
Espace en trop

## Gestion des langues dans GEDO.pdf

**Remarques**:
Extraction des Tables: Bon
Extraction des URL: Bon
Extraction générale du texte: Bon 

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
## 1.2. Langues utilisées :

Français (F) Allemand (D) Italien (I) Anglais (A) Espagnol (E)

aurait du être comme celle-ci : en ligne par ligne:
Langues utilisées :

Français (F)

Allemand (D)

Italien (I)

```
Le reste est bon, la structure est gardée, pas de mots coupés ou autre.

## Globe-trotter.pdf

**Remarques**:
Extraction des Tables: Bon
Extraction des URL: Bon
Extraction générale du texte: Bon 

Pas de problème sur cette extraction très simple (pdf d'une seule page sans image ou tableau complexe).

## Lacunes d'assurance .pdf

**Remarques**:
Extraction des Tables: Bon
Extraction des URL: Bon
Extraction générale du texte: Bon 

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
d 'obten ir

```
Un mot à mal été interprêté

## Opposition et Recours.pdf

**Remarques**:
Extraction des Tables: Bon
Extraction des URL: Bon
Extraction générale du texte: Bon 

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
l'assuré /e


```
Quelques espaces en trop sur certains mots

```text

```
Charactère non reconnu lors de l'extraction par le pipeline (représente le point d'axclamation dans un rond rouge page 2 sur 3 du PDF)


## SITAX - Support de formation - Partie Validations.pdf

**Remarques**:
Extraction des Tables: moyen
Extraction des URL: Bon
Extraction générale du texte: Bon 

Ce document comporte quelques petites simgularités notamment sur la détection générale du pipeline docling on remarque notamment :
```text
## Index :

## 1 Comprendre le masque 'Validation des taxations' ................................................................. 2 2 Valider ................................................................................................................................. 4 3 Refuser  ................................................................................................................................. 6 4 Les petits plus de SITAX… ...................................................................................................... 8 5 FAQ ...................................................................................................................................... 9

```
la table n'a pas été traitée correctement 

```text
-  Notez que même après un tri, les lignes représentant une taxation E + N (en vert) et une taxation N couple (en jaune), vont toujours rester ensemble.

-  Il est nécessaire de valider aussi la taxation N même si cette dernière sera annulée.
```
Charactère non reconnu lors de l'extraction par le pipeline (représente le point d'axclamation dans un rond rouge page 2 sur 3 du PDF)