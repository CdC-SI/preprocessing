from docling_core.types.doc.document import DoclingDocument, DocTagsDocument
from pathlib import Path
import re
import logging

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

# Configuration
DOC_NAME = "Adhésion traitement"  # CHANGER SELON LES TESTS

doctags_path = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage3_test/{DOC_NAME}/{DOC_NAME}_reordered_with_tables_pictures_url.doctags")
output_dir = Path("preprocessing/matthias_guideline_preprocessing/data/output_files/stage4_test")
output_dir.mkdir(parents=True, exist_ok=True)
md_path = output_dir / f"{DOC_NAME}.md"


def clean_doctags(content: str) -> str:
    # Nettoie le contenu doctags avant conversion.

    # 1. Supprime les balises <picture>...</picture> restantes (sur une ou plusieurs lignes)
    nb_pictures = len(re.findall(r"<picture>.*?</picture>", content, flags=re.DOTALL))
    content = re.sub(r"<picture>.*?</picture>", "", content, flags=re.DOTALL)
    _log.info(" %d balise(s) <picture> supprimée(s)", nb_pictures)

    # 2. Supprime les lignes vides consécutives laissées par la suppression
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()

CONTAINER_TAGS = [
    "unordered_list",
    "ordered_list",
    "otsl",
]

# Balises "orphelines" qui doivent être extraites hors des conteneurs
ORPHAN_TAGS = [
    "text",
    "section_header_level_1",
    "section_header_level_2",
    "section_header_level_3",
    "caption",
    "footnote",
]


def extract_orphan_tags_from_containers(content: str) -> str:
    # Extrait les balises orphelines imbriquées dans des conteneurs
    # (listes, tableaux) vers l'extérieur, juste après la balise fermante.
    # Docling ignore ces balises si elles sont imbriquées.
    nb_moved = 0
    orphan_pattern = "|".join(ORPHAN_TAGS)

    def move_orphans_out(m):
        nonlocal nb_moved
        container_tag     = m.group(1)
        container_content = m.group(2)

        # Extrait toutes les balises orphelines dans le conteneur
        orphans = re.findall(
            rf"<(?:{orphan_pattern})>.*?</(?:{orphan_pattern})>",
            container_content,
            flags=re.DOTALL,
        )
        nb_moved += len(orphans)

        # Supprime les orphelins de l'intérieur du conteneur
        container_clean = re.sub(
            rf"<(?:{orphan_pattern})>.*?</(?:{orphan_pattern})>",
            "",
            container_content,
            flags=re.DOTALL,
        )

        # Reconstruit : conteneur nettoyé + orphelins déplacés après
        result = f"<{container_tag}>{container_clean}</{container_tag}>"
        if orphans:
            result += "\n" + "\n".join(orphans)
        return result

    container_pattern = "|".join(CONTAINER_TAGS)
    content = re.sub(
        rf"<({container_pattern})>(.*?)<\/\1>",
        move_orphans_out,
        content,
        flags=re.DOTALL,
    )
    _log.info(" %d balise(s) orpheline(s) extraite(s) hors des conteneurs", nb_moved)
    return content


def clean_doctags(content: str) -> str:
    # 1. Supprime les balises <picture> restantes
    nb_pictures = len(re.findall(r"<picture>.*?</picture>", content, flags=re.DOTALL))
    content = re.sub(r"<picture>.*?</picture>", "", content, flags=re.DOTALL)
    _log.info("  %d balise(s) <picture> supprimée(s)", nb_pictures)

    # 2. Extrait les balises orphelines hors des conteneurs
    content = extract_orphan_tags_from_containers(content)

    # 3. Supprime les lignes vides consécutives
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()
def convert_doctags_to_markdown(doctags_path: Path, md_path: Path) -> None:
    _log.info("CONVERSION DOCTAGS → MARKDOWN")
    _log.info("Source : %s", doctags_path)
    _log.info("Sortie : %s", md_path)

    # Lecture
    if not doctags_path.exists():
        _log.error("Fichier introuvable : %s", doctags_path)
        return

    _log.info("Lecture du fichier doctags...")
    content = doctags_path.read_text(encoding="utf-8")
    _log.info("  %d lignes lues", content.count("\n"))

    # Nettoyage
    _log.info("Nettoyage du contenu...")
    content_clean = clean_doctags(content)

    # Conversion Docling
    _log.info("Conversion via Docling...")
    try:
        doctags    = DocTagsDocument.from_multipage_doctags_and_images(content_clean, None)
        docling_doc = DoclingDocument.load_from_doctags(doctags)
    except Exception as e:
        _log.error("Erreur lors de la conversion Docling : %s", e)
        return

    # Export Markdown
    _log.info("Export en Markdown...")
    markdown = docling_doc.export_to_markdown()

    nb_headers  = markdown.count("\n#")
    nb_lists    = markdown.count("\n-")
    nb_tables   = markdown.count("|")
    _log.info("  Statistiques du markdown généré :")
    _log.info("    - Titres   : %d", nb_headers)
    _log.info("    - Listes   : %d", nb_lists)
    _log.info("    - Cellules : %d", nb_tables)
    _log.info("    - Taille   : %d caractères", len(markdown))

    md_path.write_text(markdown, encoding="utf-8")
    _log.info("Markdown généré : %s", md_path)

if __name__ == "__main__":
    convert_doctags_to_markdown(doctags_path, md_path)