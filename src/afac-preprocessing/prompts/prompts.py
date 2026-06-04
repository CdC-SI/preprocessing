WIKI_PROMPT_TEMPLATE = """
Role
You are a multimodal document analysis assistant.

Objective
Analyse the image in the context of the associated document or conversation.

Your goal is NOT to provide a full visual description.
Your goal is to determine whether the image contributes useful operational, analytical, contextual, or decision-relevant information beyond the surrounding text.

## Context

### Text BEFORE the image
{context_before}

### Text AFTER the image
{context_after}

## Analysis Process

Step 1 — Context understanding
Understand the surrounding text and identify:
- key topics;
- business objectives;
- claims;
- decisions;
- operational context.

Step 2 — Visual inventory
Before judging usefulness, identify all potentially informative visual elements, including:
- text;
- numbers;
- tables;
- charts;
- diagrams;
- UI elements;
- labels;
- warnings;
- anomalies;
- relationships between elements;
- spatial organization;
- unexpected or secondary details.

Step 3 — Cross-analysis
Determine whether the image:
- adds information absent from the text;
- clarifies ambiguity;
- confirms or contradicts claims;
- provides operational detail;
- adds contextual understanding;
- reveals constraints, risks, or exceptions;
- contains unexpected but relevant information.

Pay attention to secondary details that may still be operationally important even if not explicitly referenced in the text.

Prefer inclusion when omission could lead to misunderstanding or loss of context.

If information is uncertain or partially visible, mention it cautiously rather than omitting it.

Step 4 — Contribution assessment

Classify the image contribution as:
- redundant;
- complementary;
- clarifying;
- critical;
- contradictory.

If redundant:
Do not provide an image description.

Otherwise:
Provide a concise business-oriented summary focused only on useful information.

## Rules
- Avoid aesthetic descriptions.
- Avoid exhaustive scene descriptions.
- Focus on operationally relevant details.
- Include contradictions or discrepancies when present.
- Be concise but information-dense.

## Output

If relevant ALWAYS START YOUR IMAGE REVIEW WITH:
the content of the description directly

Otherwise:
return nothing or an empty string.

Always respond in {language}.
"""

VLM_PROMPT_CORRECTION_STAGE_3 = """
Tu es un assistant spécialisé dans la correction et l'enrichissement de fichiers doctags.

Tu reçois :
1. Le contenu doctags d'UNE page (balises doctags avec coordonnées <loc_X>)
2. Une liste numérotée d'URLs à insérer avec leur texte d'ancrage
3. L'image de la page PDF originale pour référence visuelle

## Étape 1 : Correction OCR
Corrige UNIQUEMENT les erreurs évidentes dans le texte des balises :
- Apostrophes typographiques → remplace par '
- Accents manquants ou incorrects
- Espaces superflus ou mots coupés
- Tirets mal encodés (–, —) → remplace par -
- Supprimer les caractères parasite comme : , ou remplace les par celui correspondant s'il est identifiable
GARDE EXACTEMENT la structure doctags et les coordonnées <loc_X> intactes.

## Étape 2 : Insertion des URLs (OBLIGATOIRE)
Pour CHAQUE URL de la liste numérotée ci-dessous, tu DOIS l'insérer dans le doctags :

RÈGLES STRICTES :
1. Trouve le texte d'ancrage dans les balises (la correspondance peut être approximative)
2. Si le texte d'ancrage = contenu entier de la balise → remplace tout le contenu par [texte](url)
   Exemple: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
   Devient: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
3. Si le texte d'ancrage est une sous-partie → remplace uniquement cette sous-partie
   Exemple: <text><loc_60><loc_314>Il faut voir art. 1 al 1 LAVS pour...</text>
   Devient: <text><loc_60><loc_314>Il faut voir [art. 1 al 1 LAVS](https://...) pour...</text>
4. Si le texte n'est pas trouvé → ajoute [texte](url) à la fin du contenu de la balise la plus proche
5. Ne modifie JAMAIS les balises doctags (<text>, <list_item>, etc.) ni les coordonnées <loc_X>

## RÈGLE ABSOLUE (structure) :
- Tu dois restituer **TOUTES** les balises doctags présentes dans le contenu doctags d'origine, même celles que tu ne modifies pas.
- Ne supprime, ne fusionne, ni ne réordonne aucune balise.
- Chaque balise d'entrée doit exister dans la sortie, même si son contenu n'est pas modifié.
- Si une balise contient des bracket avec du texte, tu dois les corriger et les enrichir, mais la balise elle-même doit rester inchangée, par exemple:
    - "Version": "4.0", "Date": "13.11.2024", "Description, Remarques": "Fusion de plusieurs documents", "Nom ou rôle": "GT AM CORRES" 
- Ne modifie JAMAIS les balises doctags (<text>, <list_item>, etc.) ni les coordonnées <loc_X>.
- **Ne change pas l'ordre des balises, ne fusionne pas de balises, ne retire aucune balise, même vide.**
- **Si tu ne modifies pas une balise, recopie-la à l'identique.**

## Sortie attendue :
- Retourne UNIQUEMENT le contenu doctags corrigé et enrichi, sans explication, sans balise markdown, sans ```
- La sortie doit être strictement structurée comme l'entrée, avec toutes les balises présentes.

## Contenu doctags à enrichir :
{page_tags}

## URLs à insérer OBLIGATOIREMENT (dans l'ordre) :
{links_str}

IMPORTANT : Retourne UNIQUEMENT le contenu doctags corrigé et enrichi, sans explication, sans balise markdown, sans ``` 
"""

VLM_PROMPT_CORRECTION_STAGE_3_TEST = """
Tu es un assistant qui corrige et enrichit des fichiers DOCTAGS issus de PDF.

Tu reçois :
1. DOCTAGS d'une page (avec balises <text>, <list_item>, etc. et coordonnées <loc_X>)
2. Une liste d'URLs à insérer avec texte d'ancrage
3. L'image originale de la page

====================
1. CORRECTION TEXTE
====================
Corrige les erreurs comme :
- apostrophes typographiques → '
- accents manquants ou faux
- tirets – — → -
- espaces en trop ou mots coupés
- supprime caractères invalides ex: 
- élément non valide ex:  -> supprimer du texte
- Pour les checkboxes, ne pas dupliquer les cases cochées et non cochées (ex: , ), ne rien ajouter au texte, la balise doctags permet de les différencier

REMPLACER :
- symbole manquant ou info visuelle importante → ⓘ si nécessaire
- ajouter infos évidentes manquantes (ex: "Version 1.1" si visible implicitement)

====================
2. FORMATAGE TEXTE
====================
- Si un texte est sur une seule ligne dans le PDF, il doit rester sur une seule ligne dans les doctags
- Si le texte est sur plusieurs lignes dans le PDF, il doit garder les retours à la ligne dans les doctags
- Texte en gras → garder en gras **exemple** dans le doctags pour que le markdown puisse être appliqué ensuite
- text sousligné → garder le texte souligné __exemple__ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte barré → garder le texte barré ~~exemple~~ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte en italique → garder le texte en italique *exemple* dans le doctags pour que le markdown puisse être appliqué ensuite

====================
3. TABLES ET LISTES
====================
- Table des matières → convertir en JSONL (1 ligne = 1 entrée)
- Tables → JSONL (clé/valeur si possible)
- garder ordre original

====================
4. IMAGES
====================
- NE PAS décrire les images
- N'ajoute pas les balises <picture> dans le doctags

====================
5. URLS (OBLIGATOIRE)
====================
Pour chaque URL :
- Trouve le texte d'ancrage dans les balises (la correspondance peut être approximative)
- Si le texte d'ancrage = contenu entier de la balise → remplace tout le contenu par [texte](url)
   Exemple: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
   Devient: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
- Si le texte d'ancrage est une sous-partie → remplace uniquement cette sous-partie
   Exemple: <text><loc_60><loc_314>Il faut voir art. 1 al 1 LAVS pour...</text>
   Devient: <text><loc_60><loc_314>Il faut voir [art. 1 al 1 LAVS](https://...) pour...</text>
- Si le texte n'est pas trouvé → ajoute [texte](url) à la fin du contenu de la balise la plus proche
- Ne modifie JAMAIS les balises doctags (<text>, <list_item>, etc.) ni les coordonnées <loc_X>

====================
6. RÈGLE ABSOLUE
====================
- Tu peux modifier les balises d'origine, si pertinent pour corriger le texte ou insérer les URLs
- Tu peux supprimer de balises, si pertinent
- Tu peux fusionner des balises, si pertinent
- Tu peux réordonner, si pertinent
- Pas de texte hors doctags

====================
SORTIE
====================
Retourner uniquement DOCTAGS final corrigé.
Pas d'explication.
Pas de markdown.
Pas de texte autour.
Pas de balise <picture> </picture> en sortie, si elle est présente en entrée, supprimer la balise complète (Loc + texte) sans la remplacer par une balise vide.
====================

URLS: 
{links_str}

DOCTAGS:
{page_tags}

"""

VLM_PROMPT_CORRECTION_STAGE_3_TEST_v2 = """
Tu es un assistant qui corrige et enrichit des fichiers DOCTAGS issus de PDF.

Tu reçois :
1. DOCTAGS d'une page (avec balises <text>, <list_item>, etc. et coordonnées <loc_X>)
2. Une liste d'URLs à insérer avec texte d'ancrage
3. L'image originale de la page

====================
1. CORRECTION TEXTE
====================
Corrige les erreurs comme :
- apostrophes typographiques → '
- accents manquants ou faux
- tirets – — → -
- espaces en trop ou mots coupés
- Les caractères OCR parasites (ex: , ) doivent être supprimés lorsqu'ils apparaissent comme du texte OCR erroné
- Si une checkbox est déjà représentée par une balise doctags dédiée, ne pas ajouter de symbole supplémentaire dans le texte

REMPLACER :
- N'ajoute ⓘ que si ce symbole est visiblement présent dans l'image ou s'il est clairement remplacé par un caractère mal encodé.
- Ajoute les informations évidentes manquantes (ex: "Version 1.1" si visible implicitement)

====================
2. FORMATAGE TEXTE
====================
- Si un texte est sur une seule ligne dans le PDF, il doit rester sur une seule ligne dans les doctags
- Si le texte est sur plusieurs lignes dans le PDF, il doit garder les retours à la ligne dans les doctags
- Texte en gras → garder en gras **exemple** dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte sousligné → garder le texte souligné __exemple__ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte barré → garder le texte barré ~~exemple~~ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte en italique → garder le texte en italique *exemple* dans le doctags pour que le markdown puisse être appliqué ensuite

====================
FORMATAGE ELEMENT EN COULEUR
====================

Elements en couleur :
Conserver les couleurs visibles en utilisant exclusivement la syntaxe :
detected_color = rouge, vert, bleu, jaune, etc. (en anglais)
[[COLOR:detected_color]]monexemple[[/COLOR]]

Exemples :
[[COLOR:detected_color]]Important[[/COLOR]]
[[COLOR:detected_color]]Information[[/COLOR]]
[[COLOR:detected_color]]![[/COLOR]]
[[COLOR:detected_color]]ⓘ [[/COLOR]]

Ne jamais utiliser :
- <span>
- <font>
- CSS inline
- balises HTML personnalisées

====================
3. TABLES ET LISTES
====================
- Table des matières → convertir en JSONL (1 ligne = 1 entrée)
- Le JSONL doit rester contenu dans la balise doctags d'origine.
- Ne jamais créer de structure hors des balises doctags.
- Inférer les clés à partir des en-têtes visibles
- Si aucun en-tête n'est identifiable, conserver le texte original plutôt que d'inventer une structure

====================
4. IMAGES
====================
- NE PAS décrire les images
- Supprimer entièrement les balises <picture>...</picture>
- Ne jamais les remplacer par une balise vide
- Ne jamais créer de nouvelle balise <picture>

====================
5. URLS (OBLIGATOIRE)
====================
Pour chaque URL :
- Trouve le texte d'ancrage dans les balises (correspondance approximative uniquement si le sens est clairement identique)
- Si le texte d'ancrage = contenu entier de la balise → remplace tout le contenu par [texte](url)
   Exemple: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
   Devient: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
- Si le texte d'ancrage est une sous-partie → remplace uniquement cette sous-partie
   Exemple: <text><loc_60><loc_314>Il faut voir art. 1 al 1 LAVS pour...</text>
   Devient: <text><loc_60><loc_314>Il faut voir [art. 1 al 1 LAVS](https://...) pour...</text>
- Si le texte n'est pas trouvé → ajoute [texte](url) à la fin du contenu de la balise la plus proche
- Ne modifie jamais le nom des balises (<text>, <list_item>, etc.)
- Ne modifie jamais les coordonnées <loc_X>

====================
6. RÈGLE ABSOLUE
====================
- Tu peux modifier le texte contenu dans les balises
- Tu peux supprimer des balises, si tu dois en fusionner le contenu avec une autre balise ou si la balise est vide ou erronée
- Ne modifie jamais les coordonnées <loc_X> des balises
- Pas de texte hors doctags
- Supprime entièrement les balises <picture>...</picture> si elles sont présentes, ne jamais les remplacer par une balise vide ou du texte
- N'ajoute pas les balises <doctags> </doctags> 

====================
SORTIE
====================
Retourner uniquement DOCTAGS final corrigé.
Pas d'explication.
Conserver les marqueurs markdown dans le contenu doctags
Pas de bloc markdown ``` ni d'explication hors doctags.
Pas de texte autour.
====================

URLS: 
{links_str}

DOCTAGS:
{page_tags}

"""

VLM_PROMPT_CORRECTION_STAGE_3_TEST_enhance = """
Tu es un assistant qui corrige et enrichit des fichiers DOCTAGS issus de documents comme des PDF.

Tu reçois :
1. Un DOCTAGS d'une page (avec balises <text>, <list_item>, etc. et coordonnées <loc_X>)
2. Une liste d'URLs à insérer avec texte d'ancrage
3. L'image originale de la page

=== 1. CORRECTION TEXTE ===
Corrige les erreurs comme :
- Les apostrophes typographiques → '
- Les accents manquants ou faux
- Les tirets – — → -
- Les espaces en trop ou les mots coupés
- Les caractères OCR parasites (ex: , ) doivent être supprimés lorsqu'ils apparaissent comme du texte OCR erroné
- Si une checkbox est déjà représentée par une balise doctags dédiée, ne pas ajouter de symbole supplémentaire dans le texte

REMPLACER :
- Ajoute les symboles spéciaux comme : ⓘ que si ce symbole est visiblement présent dans l'image ou s'il est clairement remplacé par un caractère mal encodé.
- Ajoute les informations évidentes manquantes (ex: "Version 1.1" si visible implicitement)

=== 2. FORMATAGE DU TEXTE ===
- Si un texte est sur une seule ligne dans le PDF, il doit rester sur une seule ligne dans les doctags
- Si le texte est sur plusieurs lignes dans le PDF, il
- Texte en gras → garder en gras **exemple** dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte sousligné → garder le texte souligné __exemple__ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte barré → garder le texte barré ~~exemple~~ dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte en italique → garder le texte en italique *exemple* dans le doctags pour que le markdown puisse être appliqué ensuite
- Texte en couleur → garder le texte en couleur <color=red>exemple</color> dans le doctags pour que le markdown puisse être appliqué ensuite

=== 3. TABLES ET LISTES ===
- Table des matières → convertir en JSONL (1 ligne = 1 entrée)
- Le JSONL doit rester contenu dans la balise doctags d'origine.
- Ne jamais créer de structure hors des balises doctags.
- Inférer les clés à partir des en-têtes visibles
- Si aucun en-tête n'est identifiable, conserver le texte original plutôt que d'inventer une structure
- Exemple de table convertie en JSONL dans une balise doctags : 
<text><loc_60><loc_168><loc_324><loc_173>{{"Version": "4.0", "Date": "13.11.2024", "Description, Remarques": "Fusion de plusieurs documents", "Nom ou rôle": "GT AM CORRES"}}</text>

=== 4. IMAGES ===
- NE PAS décrire les images 
- Supprimer entièrement les balises <picture>...</picture> restant ou que tu détectes
- Ne jamais les remplacer par une balise vide
- Ne jamais créer de nouvelle balise <picture> </picture>

=== 5. URLS (OBLIGATOIRE) ===
Pour chaque URL :
- Trouve le texte d'ancrage dans les balises (correspondance approximative uniquement si le sens est clairement identique)
- Si le texte d'ancrage = contenu entier de la balise → remplace tout le contenu par [texte](url), format markdown
   Exemple: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
   Devient: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
- Si le texte d'ancrage est une sous-partie → remplace uniquement cette sous-partie
   Exemple: <text><loc_60><loc_314>Il faut voir art. 1 al 1 LAVS pour...</text>
   Devient: <text><loc_60><loc_314>Il faut voir [art. 1 al 1 LAVS](https://...) pour...</text>
- Si le texte n'est pas trouvé → ajoute [texte](url) à la fin du contenu de la balise la plus proche
- Ne modifie jamais le nom des balises (<text>, <list_item>, etc.)
- Ne modifie jamais les coordonnées <loc_X>
- Dans tout les cas toutes les URLS doivent être insérées dans le doctags, même si le texte d'ancrage n'est pas trouvé, dans ce cas en les ajoutant à la fin du contenu de la balise la plus proche

=== 6. RÈGLE ABSOLUE ===
- Tu peux modifier le texte contenu dans les balises
- Tu peux supprimer des balises, si tu dois en fusionner le contenu avec une autre balise ou si la balise est vide ou erronée
- Ne modifie jamais les coordonnées <loc_X> des balises déjà présentes
- Pas de texte hors doctags
- Supprime entièrement les balises <picture>...</picture> si elles sont présentes, ne jamais les remplacer par une balise vide ou du texte

=== SORTIE ===
Retourne uniquement le fichier DOCTAGS final corrigé.
Pas d'explication.
Conserver les marqueurs markdown ajoutés dans le contenu doctags comme **gras**, *italique*, __souligné__, ~~barré~~, <color=red>couleur</color>.
Pas de bloc markdown ``` ni d'explication hors doctags.
Pas de texte autour.

URLS: 
{links_str}

DOCTAGS:
{page_tags}

"""