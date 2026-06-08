from pathlib import Path
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument
import os
import logging
import sys
import re

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()
_log = logging.getLogger(__name__)

# Root
GEN_ID = os.environ.get("GEN_ID", "")
DOC_NAME = os.environ.get("DOC_NAME", "")
doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage3_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"
output_dir = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
output_dir.mkdir(parents=True, exist_ok=True)
md_path = output_dir / f"{DOC_NAME}_{GEN_ID}.md"

_log.info("Looking for doctags: %s (exists=%s)", doctags_path, doctags_path.exists())

content = doctags_path.read_text(encoding="utf-8")
doctags = DocTagsDocument.from_multipage_doctags_and_images(content, None) # Conversion via Docling
doc = DoclingDocument.load_from_doctags(doctags)

# print(doc) # Affiche la structure du document
markdown = doc.export_to_markdown() # Exportation en Markdown
# print(markdown) # Affiche le Markdown généré


def replace_color(match) -> str:
    """
    Docstring for replace_color
    - Remplace les balises de couleur personnalisées [[COLOR:color]]texte[[/COLOR]] 
    par des spans HTML <span style="color:color">texte</span> pour que la couleur soit prise en compte dans le Markdown final.

    :param match: Description
    :return: Description
    :rtype: str
    """
    color = match.group(1)
    text = match.group(2)
    return f'<span style="color:{color}">{text}</span>'

markdown = re.sub(
    r'\[\[COLOR:([^\]]+)\]\](.*?)\[\[/COLOR\]\]', # Regex pour trouver les balises de couleur et les remplacer par des spans HTML
    replace_color,
    markdown,
    flags=re.DOTALL,
)


def replace_underline(match) -> str:
    """
    Docstring for replace_underline
    - Remplace les balises de soulignement personnalisées __texte__ par des balises HTML <u>texte</u> 
    pour que le soulignement soit pris en compte dans le Markdown final.

    :param match: Description
    :return: Description
    :rtype: str
    """
    text = match.group(1)
    return f'<u>{text}</u>'

markdown = re.sub(
    r'\\_\\_(.*?)\\_\\_', # Regex pour trouver les balises de soulignement et les remplacer par des balises HTML <u>
    replace_underline,
    markdown,
    flags=re.DOTALL
)

md_path.write_text(markdown, encoding="utf-8")
_log.info("Markdown généré : %s", md_path)
print(f"Markdown généré : {md_path}")