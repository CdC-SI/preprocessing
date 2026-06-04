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
DOC_NAME = os.environ.get("DOC_NAME", "")
doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage3_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"
output_dir = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
output_dir.mkdir(parents=True, exist_ok=True)
md_path = output_dir / f"{DOC_NAME}.md"

_log.info("Looking for doctags: %s (exists=%s)", doctags_path, doctags_path.exists())

# Conversion via Docling
content = doctags_path.read_text(encoding="utf-8")
doctags = DocTagsDocument.from_multipage_doctags_and_images(content, None)
doc = DoclingDocument.load_from_doctags(doctags)

# Exportation en Markdown
markdown = doc.export_to_markdown()
print(markdown)

# Post-traitement pour gérer les balises de couleur (ex: [[COLOR:red]]texte[[/COLOR]])
# Function pour remplacer les balises de couleur par des spans HTML
def replace_color(match):
    color = match.group(1)
    text = match.group(2)
    return f'<span style="color:{color}">{text}</span>'

# Regex pour trouver les balises de couleur et les remplacer par des spans HTML
markdown = re.sub(
    r'\[\[COLOR:([^\]]+)\]\](.*?)\[\[/COLOR\]\]',
    replace_color,
    markdown,
    flags=re.DOTALL,
)

md_path.write_text(markdown, encoding="utf-8")
_log.info("Markdown généré : %s", md_path)
print(f"Markdown généré : {md_path}")