# Étape 3 – Extraction et association des liens URL enrichie par VLM

Cette étape vise à **extraire tous les liens hypertextes (URL) externes d’un PDF** puis à les associer et les injecter précisément dans le texte structuré (`.doctags`), en s’appuyant sur un modèle Vision-Language (VLM) pour corriger et enrichir le résultat.

---

## Scripts principaux

### 1. `1_get_url.py` — Extraction des liens externes

- **Chargement du PDF**
  - Ouvre le PDF cible avec PyMuPDF (`fitz`).
- **Extraction des liens**
  - Pour chaque page, détecte toutes les annotations de type lien.
  - **Seuls les liens externes** sont conservés (`http://`, `https://`, `mailto:`).
  - Pour chaque lien, extrait :
    - Le numéro de page
    - Le texte contenu dans la zone du lien (si disponible)
    - L’URL
    - Le type de lien ("URI")
    - Les détails techniques (dont la position de la box)
- **Sauvegarde**
  - Tous les liens sont sauvegardés dans un fichier `.jsonl` (JSONLines), chaque ligne correspondant à un lien détecté.

### 2. `2_url_tuning_vlm.py` — Association, correction et enrichissement par VLM

- **Chargement des données**
  - Charge les liens extraits (`.jsonl`) et le fichier `.doctags` issu des étapes précédentes.
- **Préparation page par page**
  - Pour chaque page, prépare le texte structuré et la liste des liens à insérer.
  - Génère une image de la page PDF pour fournir un contexte visuel au VLM.
- **Appel au modèle VLM**
  - Utilise un modèle Vision-Language (Qwen, etc.) pour :
    - Corriger les erreurs OCR (espaces, accents, coupures, etc.)
    - Insérer chaque URL au bon endroit dans le texte, au format Markdown `[texte](url)`
    - Garantir la conservation de la structure doctags (balises, coordonnées)
- **Reconstruction et sauvegarde**
  - Reconstruit le fichier `.doctags` enrichi, prêt pour les étapes suivantes.

## Fichiers générés

- Liste structurée de tous les liens détectés (un par ligne) : `hyperlinks_data_*.jsonl`
- Fichier doctags enrichi, liens insérés et texte corrigé : `*_url_vlm.doctags`

## Utilisation typique

1. Placez votre PDF dans le dossier d’entrée.
2. Exécutez `1_get_url.py` pour extraire tous les liens externes du document.
3. Exécutez `2_url_tuning_vlm.py` pour corriger le texte et insérer chaque lien à la bonne position, en s’appuyant sur le VLM.
4. Retrouvez les fichiers `.jsonl` et `.doctags` enrichis dans le dossier de sortie.

## Points clés

- **Extraction exhaustive** : tous les liens externes sont recensés avec leur texte, leur position et leur page.
- **Correction OCR et enrichissement** : le VLM corrige les erreurs de texte et insère les liens au bon endroit, même en cas d’ambiguïté ou d’erreur OCR.
- **Respect de la structure** : la structure doctags d’origine est conservée, aucune balise n’est supprimée ou réordonnée.
- **Interopérabilité** : les fichiers produits sont prêts pour l’indexation, la navigation enrichie ou d’autres traitements automatiques.

**En résumé :**
- Extraction des liens externes avec `1_get_url.py`
- Correction et insertion intelligente des liens avec `2_url_tuning_vlm.py` (VLM)
- Sauvegarde structurée pour enrichissement documentaire et navigation avancée