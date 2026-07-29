"""
Ontologie du knowledge graph AFAC.

Définit la liste **fermée** des types de nœuds et de relations autorisés pour l'extraction
d'entités/relations depuis les documents AFAC prétraités (`*_final.md`).

Pourquoi une liste fermée : sur un corpus de ~20 documents, un LLM sans contrainte produit des
doublons (`GEDO` / `Gedo`, `ARA` / `ara` / `Ara`, `TeleZas` / `TeleZas3`) et le graphe devient
du bruit. On borne donc les labels ET on normalise les noms d'entités (cf. NAME_ALIASES).

Format directement consommable par `neo4j_graphrag` :

    from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline
    from ontology.afac_ontology import NODE_TYPES, RELATIONSHIP_TYPES, PATTERNS

    pipeline = SimpleKGPipeline(
        llm=..., driver=..., embedder=...,
        entities=NODE_TYPES,
        relations=RELATIONSHIP_TYPES,
        potential_schema=PATTERNS,
        from_pdf=False,
    )
"""
from __future__ import annotations

# Types de nœuds (labels fermés)
# Chaque entité porte au minimum une propriété `name` (le nom normalisé, cf. normalize_name).
NODE_TYPES: list[dict] = [
    {
        "label": "Document",
        "description": "Un aide-mémoire / guide opérationnel AFAC (un fichier PDF source). "
                       "Ex. « Adhésion d'un mineur.pdf », « Confirmer l'adhésion.pdf ».",
        "properties": [
            {"name": "name", "type": "STRING"},
            {"name": "version", "type": "STRING"},
        ],
    },
    {
        "label": "Theme",
        "description": "Thème métier regroupant des documents — correspond aux dossiers de "
                       "data/output_files_preprocessing (ex. Mineur, Détachement, Globe-trotter).",
        "properties": [{"name": "name", "type": "STRING"}],
    },
    {
        "label": "System",
        "description": "Application ou système informatique métier utilisé dans une procédure. "
                       "Ex. GEDO, TeleZas3, SITAX, ARA.",
        "properties": [{"name": "name", "type": "STRING"}],
    },
    {
        "label": "Code",
        "description": "Code d'action, motif ou référence opérationnelle codifiée. "
                       "Ex. ARC 31, ARC 61, ARC 98, motif 7/8.",
        "properties": [
            {"name": "name", "type": "STRING"},
            {"name": "signification", "type": "STRING"},
        ],
    },
    {
        "label": "Process",
        "description": "Processus métier formalisé, typiquement référencé par un lien bpanda. "
                       "Ex. « CSC AF - Traiter les demandes d'adhésion ».",
        "properties": [
            {"name": "name", "type": "STRING"},
            {"name": "url", "type": "STRING"},
        ],
    },
    {
        "label": "Concept",
        "description": "Notion métier du domaine de l'assurance facultative : statut, objet ou "
                       "terme spécialisé. Ex. mineur, majeur, adhésion, NAVS, date d'effet, R+F.",
        "properties": [{"name": "name", "type": "STRING"}],
    },
    {
        "label": "LegalRef",
        "description": "Référence légale ou réglementaire (article de loi, ordonnance, seuil "
                       "légal). Ex. « majorité fixée à 18 ans », article LAVS.",
        "properties": [{"name": "name", "type": "STRING"}],
    },
    {
        "label": "Condition",
        "description": "Condition, critère d'éligibilité ou règle à remplir pour qu'une action "
                       "s'applique. Ex. « 5 ans d'assurance préalable », « domicile en Suisse ».",
        "properties": [{"name": "name", "type": "STRING"}],
    },
]


# Types de relations (labels fermés)
RELATIONSHIP_TYPES: list[dict] = [
    {"label": "PART_OF",
     "description": "Appartenance / rattachement (un Document fait partie d'un Theme)."},
    {"label": "APPLIES_TO",
     "description": "S'applique à un statut/objet (une procédure APPLIES_TO un Concept)."},
    {"label": "REQUIRES",
     "description": "Exige, en préalable, une Condition, un System, un Code ou un Concept."},
    {"label": "TRIGGERS",
     "description": "Déclenche une action, un Code ou un Process."},
    {"label": "EXCLUDES",
     "description": "Exclut / interdit (une Condition EXCLUDES un Concept ou une action)."},
    {"label": "REFERENCES",
     "description": "Fait référence à un Process, une LegalRef ou un autre Document."},
    {"label": "MENTIONS",
     "description": "Relation générique : un Document mentionne une entité, sans lien plus "
                    "spécifique identifié."},
]

# Patterns autorisés : (source_label, RELATION, target_label)
# Guide le LLM sur les combinaisons plausibles et évite les arêtes incohérentes.
PATTERNS: list[tuple[str, str, str]] = [
    ("Document", "PART_OF",    "Theme"),
    ("Document", "REFERENCES", "Process"),
    ("Document", "REFERENCES", "LegalRef"),
    ("Document", "REFERENCES", "Document"),
    ("Document", "APPLIES_TO", "Concept"),
    ("Document", "MENTIONS",   "System"),
    ("Document", "MENTIONS",   "Code"),
    ("Document", "MENTIONS",   "Concept"),

    ("Concept",   "REQUIRES",  "Condition"),
    ("Concept",   "REQUIRES",  "Code"),
    ("Concept",   "REQUIRES",  "System"),
    ("Concept",   "EXCLUDES",  "Concept"),

    ("Condition", "EXCLUDES",  "Concept"),
    ("Condition", "TRIGGERS",  "Code"),
    ("Condition", "TRIGGERS",  "Process"),

    ("Code",      "TRIGGERS",  "Process"),
    ("Code",      "REQUIRES",  "System"),
    ("Process",   "APPLIES_TO", "Concept"),
]

# Normalisation des noms d'entités
# Le scan du corpus montre des variantes de casse/orthographe pour la même entité. On les
# rabat sur une forme canonique AVANT insertion dans Neo4j pour éviter les nœuds dupliqués.
NAME_ALIASES: dict[str, str] = {
    "gedo": "GEDO",
    "ara": "ARA",
    "sitax": "SITAX",
    "telezas": "TeleZas3",
    "telezas3": "TeleZas3",
    "avs/ai": "AVS/AI",
    "avs ai": "AVS/AI",
    "navs": "NAVS",
    "r+f": "R+F",
    "bpanda": "bpanda",
}


def normalize_name(name: str) -> str:
    """Retourne la forme canonique d'un nom d'entité (déduplication).

    Applique les alias connus (insensible à la casse) puis, à défaut, renvoie le nom
    d'origine simplement débarrassé des espaces superflus.
    """
    if not name:
        return name
    key = " ".join(name.strip().split()).lower()
    return NAME_ALIASES.get(key, name.strip())


# Ensembles pratiques pour validation en aval (chargement, tests).
NODE_LABELS: set[str] = {n["label"] for n in NODE_TYPES}
RELATION_LABELS: set[str] = {r["label"] for r in RELATIONSHIP_TYPES}


if __name__ == "__main__":
    # Aperçu rapide de l'ontologie.
    print(f"{len(NODE_TYPES)} types de nœuds : {sorted(NODE_LABELS)}")
    print(f"{len(RELATIONSHIP_TYPES)} types de relations : {sorted(RELATION_LABELS)}")
    print(f"{len(PATTERNS)} patterns autorisés")
    for s, r, t in PATTERNS:
        print(f"  ({s}) -[:{r}]-> ({t})")
    print("\nExemples de normalisation :")
    for raw in ["gedo", "Ara", "TeleZas", "  AVS/AI ", "Concept inconnu"]:
        print(f"  {raw!r:20} -> {normalize_name(raw)!r}")
