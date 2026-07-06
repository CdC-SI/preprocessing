# Stage 5 — Génération des métadonnées

Ce stage collecte, enrichit et stocke les métadonnées structurées de chaque document traité par le pipeline. Il s'exécute après les stages 1 à 11 et produit une ligne CSV par document avec trois colonnes : **CONTENT**, **METADATA** et **EMBEDDING**.

---

## Comment les champs sont collectés

### Identité & localisation
| Champ | Comment il est obtenu |
|---|---|
| `uuid` | UUID déterministe (v5) dérivé du chemin relatif du document, un même document obtient toujours le même identifiant d'un run à l'autre. |
| `user_uuid` | Laissé vide à la génération, renseigné ultérieurement par la couche applicative. |
| `source` | Toujours `"afac"`,  nom fixe du corpus documentaire. |
| `title` | Nom du fichier original (avec extension). |
| `visibility` | Par défaut `"internal"`. |
| `language` | Par défaut `"fr"`. |

### Structure du document
| Champ | Comment il est obtenu |
|---|---|
| `doctype` | Lu depuis le mimetype présent dans le JSON Docling du Stage 1 (ex. `"pdf"`, `"docx"`). |
| `version` | Extraite de la première table du document qui possède une colonne `"Version"` (JSON Stage 1). Retourne la dernière valeur non vide de cette colonne. |
| `page_count` | Nombre d'entrées de pages dans le JSON Docling du Stage 1. |
| `page_num` | Liste des numéros de page séparés par des virgules, ex. `"1,2,3"` pour un document de 3 pages. |

### Dates
| Champ | Comment il est obtenu |
|---|---|
| `created_at` | Lue depuis les métadonnées internes du PDF via PyMuPDF. Retournée en ISO 8601. |
| `updated_at` | Horodatage du moment où le Stage 5 s'est exécuté (heure UTC courante). |

### Hiérarchie des dossiers
Ces champs sont déduits de la position du document dans `folder_source/`, le dossier qui reflète l'arborescence documentaire d'origine.

| Champ | Comment il est obtenu |
|---|---|
| `parent_label` | Nom du dossier parent immédiat (ex. `["DISPENSE"]`). |
| `children_label` | Sous-dossiers présents dans ce même dossier parent. |
| `sibling` | Autres fichiers présents dans ce même dossier parent. |

### Liens
| Champ | Comment il est obtenu |
|---|---|
| `outgoing_links` | Liste des hyperliens extraits du document au Stage 3 (texte, URL, numéro de page). |
| `incoming_links` | Liens trouvés dans les *autres* documents (sorties du Stage 3) qui référencent ce document par son nom. |

### Médias
| Champ | Comment il est obtenu |
|---|---|
| `media_type` | Liste des fichiers image extraits du document au Stage 2 (dossier `used_images/`). |

### Référence au contenu
| Champ | Comment il est obtenu |
|---|---|
| `content` | Nom du fichier markdown produit au Stage 11 : `<nom_doc>_final_embed.md` s'il existe (tables remplacées par du JSONL, produit par `markdown_tables_to_jsonl_modular.py --embed-output`, pipeline v3), sinon `<nom_doc>_final.md`. |
| `chunk_count` | Nombre de fichiers markdown trouvés pour ce document dans le Stage 11. |

---

## Champs enrichis par le VLM (Stage 5)

Ces trois champs sont générés en envoyant le contenu markdown du Stage 11 (`_final.md`) à un modèle de langage (VLM) :

| Champ | Ce qu'il contient |
|---|---|
| `resume` | Un résumé court du document rédigé par le VLM. Sauvegardé dans `resume.md`. |
| `intent` | Liste d'intentions utilisateur que le document pourrait satisfaire (ex. "comment annuler une dispense"), séparées par des virgules. Sauvegardée dans `intent.json`. |
| `hyq` | Liste de questions hypothétiques qu'un utilisateur pourrait poser et dont la réponse se trouve dans ce document. Améliore la pertinence de la recherche. Sauvegardée dans `hyq.json`. |

---

## Embedding (Stage 5)

Le contenu markdown complet du Stage 11 est envoyé à un modèle d'embedding — `_final_embed.md`
s'il existe (tables en JSONL, pipeline v3, cf. `markdown_tables_to_jsonl_modular.py`),
sinon `_final.md` (v1/v2/baseline, comportement inchangé). Le vecteur produit est :
- Sauvegardé dans `embedding.json` dans le dossier de sortie du Stage 5.
- Écrit sous forme de flottants séparés par des virgules dans la colonne **EMBEDDING** du CSV final.

Le champ `embedding_model` dans les métadonnées indique quel modèle a été utilisé.

---

## Sortie

Chaque document produit une ligne ajoutée (ou remplacée) dans un fichier CSV :

```
CONTENT | METADATA | EMBEDDING
```

- **CONTENT** : le texte markdown complet du document (sortie Stage 11, `_final.md`).
- **METADATA** : tous les champs ci-dessus, sérialisés en JSON.
- **EMBEDDING** : le vecteur d'embedding sous forme de flottants séparés par des virgules.

Le fichier de sortie est écrit dans :
```
data/output_files_preprocessing/<nom_doc>/metadata/<nom_doc>_final.csv
```

L'opération est **idempotente** : relancer le script pour un même document remplace la ligne existante.
