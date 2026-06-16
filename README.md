# CLI AFAC PREPROCESSING pour automatisation
## Introduction

L'objectif de ce projet est de proposer une version modulaire du pipeline de prétraitement (preprocessing) des documents AFAC via une interface en ligne de commande (CLI).
Cette approche vise à améliorer la maintenabilité, la flexibilité et l'automatisation du pipeline existant.

## Objectif 
### Rendre les scripts plus modulaires et moins interdépendants

L'utilisation de `Typer`, une bibliothèque Python permettant de construire facilement des applications CLI, permettra de découpler les différentes étapes du pipeline et de les rendre plus réutilisables.

À terme, il sera possible de :

- Choisir dynamiquement les paramètres d'entrée et de sortie.
- Exécuter certaines étapes de manière indépendante.
- Faciliter l'exécution parallèle des traitements lorsque cela est possible.
- Simplifier l'intégration du pipeline dans des workflows d'automatisation plus larges.

### Limites du pipeline actuel

Le pipeline actuel impose une exécution strictement séquentielle :

Le Stage 2 dépend obligatoirement des résultats du Stage 1.
Le Stage 3 dépend obligatoirement des résultats du Stage 2.
Et ainsi de suite.

Cette architecture crée des dépendances fortes entre les scripts et limite les possibilités d'exécution partielle ou parallèle.

## Vision cible

L'objectif est de rendre chaque étape du pipeline autonome, configurable et réutilisable via une interface CLI unique.
Chaque module devra pouvoir :

- Être exécuté indépendamment.
- Accepter des chemins d'entrée personnalisés.
- Produire des sorties configurables.
- Être intégré facilement dans un pipeline automatisé ou orchestré.

### Axes d'amélioration identifiés

L'analyse du pipeline actuel met en évidence plusieurs opportunités d'optimisation, tant au niveau du traitement documentaire que des appels aux modèles de vision (VLM).

## Rationalisation du pipeline Docling

Actuellement, le pipeline Docling est exécuté à plusieurs reprises avec des objectifs distincts, en s'appuyant notamment sur EasyOCR.

1. Extraction du contenu principal

Une première exécution est utilisée pour extraire le contenu du document dans plusieurs formats :
- .txt (texte brut)
- .json
- .doctags
- .md (Markdown)

2. Extraction des tableaux

Une seconde exécution du pipeline Docling est dédiée à l'extraction des tableaux structurés :
- .html
- .csv

Cette séparation des traitements constitue un premier axe de réflexion pour la modularisation du pipeline. Une architecture CLI permettrait notamment de lancer uniquement les étapes nécessaires selon les besoins du traitement.

## Optimisation des appels aux VLM

Le pipeline actuel réalise plusieurs appels successifs à un modèle de vision-langage (VLM) tel que Qwen ou un modèle équivalent.

1. Description des images
Lors du Stage 2, un premier appel est effectué afin de générer une description textuelle des images présentes dans le document.

2. Enrichissement et correction des Doctags
Un second appel est utilisé pour :
- détecter et compléter les URLs présentes dans le document
- corriger certaines erreurs de transcription ou de structuration dans le fichier .doctags

3. Validation finale du Markdown

Un troisième appel intervient en fin de pipeline afin de comparer le document Markdown généré avec le document source (PDF, DOCX, etc.).
L'objectif est de
- détecter les éventuelles pertes d'information
- corriger les erreurs résiduelles
- garantir que le document source reste la référence de vérité (source of truth) tout au long du processus

## Bénéfices attendus de la modularisation

La mise en place d'une architecture CLI modulaire permettra :

- d'exécuter chaque étape indépendamment
- de réutiliser les résultats intermédiaires sans retraiter l'ensemble du pipeline
- de faciliter l'expérimentation de nouveaux modèles OCR ou VLM
- d'améliorer la parallélisation de certains traitements
- de réduire les coûts de calcul en évitant les exécutions redondantes
- de simplifier l'intégration dans des workflows automatisés ou des orchestrateurs de tâches

## Isolation des processus et configuration

Un aspect central de cette refonte consiste à isoler au maximum les différentes étapes du pipeline afin de réduire les dépendances entre les scripts et de favoriser leur réutilisation.

Chaque étape devra être conçue comme un composant autonome disposant :
- d'entrées clairement définies
- de sorties standardisées
- d'une configuration indépendante
- d'une logique métier découplée des autres modules

L'objectif est de limiter les dépendances directes entre les scripts Python afin de faciliter leur maintenance, leur évolution et leur intégration dans différents environnements d'exécution.

Pour atteindre cet objectif, une partie importante de la configuration du pipeline pourra être externalisée dans des fichiers de configuration (par exemple au format YAML). Cette approche permettra notamment :
- de modifier le comportement du pipeline sans modifier le code source
- de sélectionner dynamiquement les modèles OCR ou VLM à utiliser
- de définir les chemins d'entrée et de sortie
- de paramétrer les options d'exécution de chaque étape
- de faciliter le déploiement sur différents environnements

À terme, la CLI devra agir comme une couche d'orchestration capable de lire ces configurations et d'exécuter uniquement les modules nécessaires en fonction du workflow demandé.
