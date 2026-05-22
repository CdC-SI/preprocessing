from pathlib import Path
from dotenv import load_dotenv
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument
import os
import logging

# Logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
_log = logging.getLogger(__name__)

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
_log.info("Loading dotenv from: %s | exists: %s", dotenv_path.resolve(), dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

DOC_NAME = os.environ.get("DOC_NAME", "")
doctags_path = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage3_test/{DOC_NAME}/{DOC_NAME}_reordered_with_tables_pictures_url.doctags")
output_dir = Path("preprocessing/matthias_guideline_preprocessing/data/output_files/stage4_test")
output_dir.mkdir(parents=True, exist_ok=True)
md_path = output_dir / f"{DOC_NAME}.md"

# Conversion via Docling
content = doctags_path.read_text(encoding="utf-8")
doctags = DocTagsDocument.from_multipage_doctags_and_images(content, None)
doc = DoclingDocument.load_from_doctags(doctags)
markdown = doc.export_to_markdown()

md_path.write_text(markdown, encoding="utf-8")
_log.info("Markdown généré : %s", md_path)