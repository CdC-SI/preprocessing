# Pour stage 1:
## Extraire les formats ciblés avec docling
Un seul script maintenant génère:
- Pipeline docling custom conversion
    - .md
    - .txt
    - .json
    - .doctags
Lorsque la task est appelée ici, il faut toujours lancer la task du task-stage1-reorder-doctags.yaml après pour générer le doctags réordonné
- pipeine docling extrait les tables:
    - .html
    - .csv
Lorsque la task est appelée ici, il faut toujouts lancer la task du task-stage1-csv-to-jsonlines.yaml pour générer le json sur le csv associé

# Pour stage 2:
- Pour lancer la description d'image avec le contexte, -> il faut le résultat des tasks : [task-stage1-ocr-export.yaml, task-stage1-csv-to-jsonlines.yaml]

- Indépendemment, pour loader les tables (converties en jsonlines) dans le doctags il faut les résultats des task :
[task-stage1-ocr-export.yaml, task-stage1-csv-to-jsonlines.yaml, task-stage1-csv-to-jsonlines.yaml]

# Pour stage 3:
- Script d'extraction des urls en jsonlines, il faut pour effectuer cette task simplement le pdf de base
- Script ajout des urls dans le doctags: 
    - ici il faut en sortie le résultat de la task task-stage3-url-extraction.yaml pour ensuite le réinjecter dans le doctags

# Pour stage 4:
- Conversion Doctags to markdown
- Correction avec le VLM des erreurs dans le markdown AVEC le pdf de base 

# Open question :
Possibilité de fusionner les deux étapes de vérification en une seule avec Qwen pour la vérification des erreurs et des tableaux, etc