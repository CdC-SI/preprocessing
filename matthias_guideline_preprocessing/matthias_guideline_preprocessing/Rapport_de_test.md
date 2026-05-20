## PROTOCOLE DE TEST:

**Contrôler les chemind d'accès avant de lancer les scripts**

Fichier analysé : **Adhésion traitement.pdf**
stage 1 : Découpage du PDF
- lancer **pipeline_multietape.py** :
```python
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
```
- en sortie dans le dossier **Adhésion traitement** il y a bien les 4 fichiers générés (.doctags, .json, .md, .txt)

- lancer **OpenCV_test_checker** :
```python
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
```
- en sortie dans **opencv_doctags_allpages_Adhésion traitement** il y a bien les 7 images détectées
- (Optionnel) lancer **stage1_csv_jsonline.py** pour convertir tout le doctags généré en jsonline

stage 2 : Description des Tables et des images puis les réinjecter dans le doctags
- lancer **test_image_decription.py** ou **test_image_description_context.py**
```python 
DOC_NAME = "Adhésion traitement" # CHANGER SELON LES TESTS
```
- contrôler en sortie dans **stage2_test/Adhésion traitement** et **.../used_images**
1) Il y a bien la présence des 7 images dans le dossier qui sont toutes conservées si necessaire
2) Il y a bien la création du **Adhésion traitement_image_descriptions.md** des descriptions générées chacune des images quand le VLM peut
3) Il y a bien la création du **Adhésion traitement_with_pictures.doctags** qui aremplace dans les balises <picture></picture> la description générée par le VLM par les balises <picture_description>

- lancer **stage2_export_table_docling.py**
```python
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
```
- le script génère un fichier .csv .html par table détectée par la pipeline docling

- lancer **stage2_csv_json.py** pour générer le fichier jsonline corrspondant aux tables 

stage 3 : Extraction des lien URL et ajout du lien dans le doctags:
- lancer **get_url.py** pour obtenir le fichier jsonline qui contient tous les liens détectés dans le fichier PDF
```python
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
```

- lancer **matchURL.py** pour réinjecter dans le fichier doctag le lien url au format markdown comme suit : **[DAF] (https://www.bsvlive.admin.ch/vollzug/documents/view/1699/lang:fre/category:22)**.</br>
```python
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
```

Le nouveau fichier doctags généré contient donc en plus la description des images qui remplace les balise <picture> par <text> et ajoute les lien URL au texte associé.

stage 4 : Finalisation du processus, convertion du fichier doctags en markdown pour être injecté dans le prompt:

## RAPPEL ORDRE UTILISATION DES SCRIPTS:
1) stage1 **pipeline_multietape.py**
2) stage1 **OpenCV_test_checker**
3) stage2 **test_image_decription.py**
4) stage2 **stage2_export_table_docling.py**
5) stage2 **stage2_csv_json.py**
6) stage3 **get_url.py**
7) stage3 **matchURL.py**
8) stage4 ***convert_doctags_to_markdown.py**

### RESULTATS:

Au final, il n'y a un nouveau fichier markdown (**Adhésion traitement_with_pictures_url.md**) généré qui à décomposé l'architecture du PDF initialement injesté, pour en extraire progressivement le contenu et produire en sortie un document plus simple à analyser par le LLM (GPT_OSS).