from docling_core.types.doc.document import DoclingDocument, DocTagsDocument
from pathlib import Path

# Chemin d'entrée
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
doctags_path = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage3_test/{DOC_NAME}/{DOC_NAME}_with_pictures_tables_url.doctags")

# Chemin de sortie (stage4_test)
output_dir = Path("preprocessing/matthias_guideline_preprocessing/data/output_files/stage4_test")
output_dir.mkdir(parents=True, exist_ok=True)
md_path = output_dir / (doctags_path.stem + ".md")

# Conversion
doctags = DocTagsDocument.from_multipage_doctags_and_images(doctags_path, None)
docling_doc = DoclingDocument.load_from_doctags(doctags)
with open(md_path, "w", encoding="utf-8") as f:
    f.write(docling_doc.export_to_markdown())

print(f"Markdown généré : {md_path}")
