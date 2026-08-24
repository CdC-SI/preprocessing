# Rapport d'erreurs atypiques et résolutions

Journal des anomalies de pipeline qui ne se résolvent pas par un simple
`--from-step` (cf. [cli-options.md](cli-options.md#5-recettes)) — cas où un
fichier intermédiaire a dû être inspecté et corrigé à la main. Chaque nouvelle
anomalie de ce type s'ajoute en fin de fichier avec le template ci-dessous.

Pour les échecs déjà couverts par la recette standard (reprendre avec
`--from-step`), inutile de consigner ici — ce fichier est réservé aux cas où
la cause est en amont du pipeline (extraction Docling, structure du PDF, etc.)
et où la correction n'est pas automatisable.

## Template

```
## Document en erreur — <nom du document>

1) Anomalie détectée
   - Étape en échec : <nom de l'étape, ex. markdown-control>
   - Message : <message d'erreur exact>
   - Cause racine : <ce qui, en amont, produit ce symptôme>

2) Méthode de résolution
   - <fichier modifié, changement exact, étape de reprise>
```

---

## Document en erreur — TN - Fortune et revenu acquis sous forme de rente

1) Anomalie détectée
   - Étape en échec : `markdown-control` (étape 10)
   - Message : `Inconsistency: 2 page(s) in the markdown but 3 page(s) in the PDF (TN - Fortune et revenu acquis sous forme de rente.pdf). Rerun markdown-convert to regenerate TN - Fortune et revenu acquis sous forme de rente_url_vlm.md with the separators <!-- page-break -->.`
   - Cause racine : le PDF source a 3 pages, mais Docling (`docling-extract`,
     étape 01) n'a détecté un tag `<page_footer>` que sur la page 1 — le
     footer de la page 2 (« Page 2 sur 3 ») est sorti comme un `<text>`
     normal au lieu d'un `<page_footer>`. `reorder_doctags.py::split_pages()`
     a un arbitrage footer vs `<page_break>` prévu pour ce genre de cas
     (cf. son docstring, précédent similaire sur *TN - Allègement dès
     01.2025*), mais ici les deux stratégies échouaient à retomber sur 3
     pages : par footer → 2 pages (page 1 seule, puis pages 2+3 fusionnées) ;
     par `<page_break>` → les deux marqueurs de rupture se retrouvaient
     collés sans contenu entre eux (le paragraphe de la page 2 avait été
     fusionné par Docling avec la fin de la page 1 dans un seul élément
     `<text>`), donc une page vide qui disparaît à la reconstruction.
     `markdown-control` a détecté l'incohérence (2 pages reconstruites vs 3
     réelles) et bloqué le pipeline plutôt que de laisser passer une
     structure de pages faussée.

2) Méthode de résolution
   - Fichier modifié : `<doc>.doctags` (sortie brute de `docling-extract`,
     avant `reorder-doctags`).
   - Changement : le tag `<text>...Page 2 sur 3</text>` a été retagué en
     `<page_footer>...Page 2 sur 3</page_footer>`, pour refléter ce que
     Docling aurait dû produire. Avec deux footers réels (page 1 et page 2),
     le split par footer retombe sur 3 pages.
   - Reprise : `uv run afac-preprocess run --input <pdf> --from-step reorder-doctags`
   - Validation : `uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing --json`
     ne remonte plus aucune entrée `issues` pour ce document ; `_final.md`,
     `metadata/` et le CSV global sont régénérés.
