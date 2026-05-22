from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling.datamodel.vlm_engine_options import (
    ApiVlmEngineOptions,
    VlmEngineType,
)
from docling.datamodel.pipeline_options import (
    VlmConvertOptions,
    VlmPipelineOptions,
    PictureDescriptionVlmEngineOptions,
    PdfPipelineOptions,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat

import os
import re
import time
import logging
import argparse
import certifi
import re
import time
import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # Je suis sur le l'.env.test qui est le même que le .env
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Certificat CA personnalisé si fourni, sinon fallback sur certifi (VU avec M Gianelli, pour les autres machines, demander accès)
custom_ca = os.environ.get("VLM_CA_PEM")
if custom_ca:
    os.environ.setdefault("SSL_CERT_FILE", custom_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", custom_ca)
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# Vérifcation de la présence des variables d'environnement nécessaires
VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(
        f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL or export it in the environment."
    )

print(f"VLM_URL: {VLM_URL}, \nVLM_MODEL_NAME: {VLM_MODEL_NAME}")# affiche dans la console les variables d'environnement chargées pour vérification

# Description des images, pas de chagement par rapport à pipeline_base.py
picture_desc_options = PictureDescriptionVlmEngineOptions.from_preset(
    "qwen",
    engine_options=ApiVlmEngineOptions(
        runtime_type=VlmEngineType.API,
        url=VLM_URL,
        params={
            "model": VLM_MODEL_NAME,
            "max_tokens": 1000,
            "skip_special_tokens": True,
        },
        timeout=30,
    ),
)

language = "french"

# Ajuster le prompt du VLM pour la description d'images, 
# avec des instructions spécifiques pour extraire uniquement les informations techniques pertinentes 

picture_desc_options.prompt = f"""

You are processing images for a retrieval system.

Your task:
Extract ONLY information that is useful for understanding processes, workflows, tables, structured data, diagrams, or technical content.

IGNORE and DO NOT describe:
- Logos
- Decorative illustrations
- Stock photos
- Portraits of people
- Background images
- Icons without technical meaning
- Purely aesthetic graphics

If the image does NOT contain informative technical content:
- Respond with an **empty string**!

If the image contains:
- A table → extract its structured content in text form.
- A process/workflow → describe the steps clearly.
- A diagram → describe components and relationships.
- A chart → summarize axes, variables and key values.
- Arrows that describe flows or connections keep them with for exemple: "X → Y" or "X --(label)--> Y". Keep the arrow direction.

Rules:
- Be concise.
- No speculation.
- No generic phrases like "This image shows".
- No decorative commentary.
- Always respond in **{language}**.

"""

# Options du VLM de base pas de changement par rapport à pipeline_base.py
vlm_options = VlmConvertOptions.from_preset(
    "qwen",
    engine_options=ApiVlmEngineOptions(
        runtime_type=VlmEngineType.API,
        url=VLM_URL,
        params={
            "model": VLM_MODEL_NAME,
            "max_tokens": 4096,
            "skip_special_tokens": True,
        },
        timeout=90,
    ),
)

# Ajuster le prompt du VLM pour l'extraction de tables, 
# avec des instructions spécifiques pour extraire toutes les tables en markdown, 
# même les petites ou mal formées, et inclure les titres si présents, sans rien manquer
vlm_options.model_spec.prompt = f"""
You are extracting content from a document page for a retrieval system.

Extract ALL content present on the page, preserving its structure and order:

1. **Titles and headings** → output as Markdown headings (`#`, `##`, `###`…)
2. **Paragraphs and plain text** → output as plain text, one paragraph per block.
3. **Lists** → output as Markdown lists (`-` or `1.`)
4. **Tables** → output using standard Markdown table syntax:
   - Use `|` to separate columns.
   - Use a header separator line with dashes (`---`) after the header row.
   - Include all rows and columns, even if some cells are empty.
   - If a table has a title or caption, include it as a bold line (**Title**) immediately above.
   - Align columns for readability.
   - Arrows that describe flows or connections keep them with for exemple: "X → Y" or "X --(label)--> Y". Keep the arrow direction.

5. **Captions or footnotes** → output as plain text below the relevant element.

Rules:
- Preserve the reading order of the page.
- Do NOT skip any text, title, paragraph, list or table.
- Do NOT describe logos, decorative images or purely aesthetic content.
- Do NOT add commentary or speculation.
- Be exhaustive: every piece of text on the page must appear in the output.

Always respond in **{language}**.
"""

# Options de la Pipeline, voir docling documentation pour les détails, pas de changement par rapport à pipeline_base.py
pdf_pipeline_options = VlmPipelineOptions(
    vlm_options=vlm_options,
    do_picture_description=True,
    picture_description_options=picture_desc_options,
    enable_remote_services=True,
)

# Converter défini au niveau du module, réutilisable dans tous les tests, avec VLM pour l'extraction de tables et description d'images
converter = DocumentConverter(
    allowed_formats=[InputFormat.PDF],
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_cls=VlmPipeline,
            pipeline_options=pdf_pipeline_options,
        ),
    },
)

# Function pour extraire les tables détectées par docling et les exporter en CSV + HTML

def export_tables_from_conv(conv_res, out_dir: Path):
    
    # Exporte les tables détectées par docling
    # Si table détectée par docling, elle est stockée en CSV + HTML 
    # (avec la table exportée en HTML par docling si possible, 
    # sinon avec le rendu HTML de pandas)
    # Et tout le LaTeX présent dans le markdown est aussi exporté en CSV + HTML,
    
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    detected_tables = getattr(conv_res.document, "tables", []) or []
    _log.info("Docling detected %d table(s).", len(detected_tables))
    print(f"Detected tables count: {len(detected_tables)}") # affiche le nombre de tables détectées par docling dans le terminal

    stem_obj = getattr(conv_res.input, "file", None)
    stem = stem_obj.stem if stem_obj is not None else "document"

    # 1. docling-detected tables
    for idx, table in enumerate(detected_tables, start=1):
        try:
            df = table.export_to_dataframe(doc=conv_res.document)
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                _log.warning("Table %d is empty after cleanup, skipping.", idx)
                continue
        except Exception as e:
            _log.warning("Failed to export table %d to DataFrame: %s", idx, e)
            continue

        print(f"## Docling Table {idx}")
        try:
            print(df.to_markdown(index=False))
        except Exception:
            pass

        csv_path = out_dir / f"{stem}-table-{idx}.csv"
        html_path = out_dir / f"{stem}-table-{idx}.html"
        _log.info("Saving CSV  → %s", csv_path)
        df.to_csv(csv_path, index=False, encoding="utf-8")
        _log.info("Saving HTML → %s", html_path)
        try:
            with html_path.open("w", encoding="utf-8") as fh:
                fh.write(table.export_to_html(doc=conv_res.document))
        except Exception:
            with html_path.open("w", encoding="utf-8") as fh:
                fh.write(df.to_html(index=False))

    # 2. LaTeX fallback — ALWAYS, ONCE, OUTSIDE THE LOOP
    md = conv_res.document.export_to_markdown()
    latex_matches = list(re.finditer(r"\\begin\{tabular\}.*?\\end\{tabular\}", md, flags=re.S))
    _log.info("Found %d LaTeX tabular block(s) in markdown.", len(latex_matches))

    for latex_idx, m in enumerate(latex_matches, start=1):
        block = m.group(0)
        body = re.sub(
            r"\\begin\{tabular\}\{.*?\}|\\end\{tabular\}|\\hline", "", block, flags=re.S
        )
        rows = [r.strip() for r in body.split(r"\\") if r.strip()]
        parsed = [[c.strip() for c in row.split("&")] for row in rows]
        if not parsed:
            continue
        try:
            header, *data_rows = parsed
            df_latex = pd.DataFrame(data_rows, columns=header) if data_rows else pd.DataFrame(parsed)
        except Exception:
            df_latex = pd.DataFrame(parsed)
        df_latex = df_latex.dropna(how="all").dropna(axis=1, how="all")
        if df_latex.empty:
            continue
        csv_path = out_dir / f"{stem}-latex-table-{latex_idx}.csv"
        html_path = out_dir / f"{stem}-latex-table-{latex_idx}.html"
        df_latex.to_csv(csv_path, index=False, encoding="utf-8")
        with html_path.open("w", encoding="utf-8") as fh:
            fh.write(df_latex.to_html(index=False))
        print(f"Saved LaTeX table {latex_idx} → {csv_path}")

    _log.info(
        "Total exported: %d docling + %d LaTeX table(s).",
        len(detected_tables),
        len(latex_matches),
    )

def export_markdown_tables_from_markdown(md_text: str, out_dir: Path, stem: str):
    
    # Exporter les tables présentes dans le markdown, 
    # même celles qui n'ont pas été détectées par docling comme des tables formelles 
    # (ex: tables avec emojis, ou mal formatées)
    
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    table_pattern = re.compile(
        r'(?:^\*\*.*\*\*\s*\n)?'       # Optional bold title
        r'(^\|.*\|\s*\n)'              # Header row
        r'(^\|[\s:-]+\|\s*\n)'         # Separator line
        r'((?:^\|.*\|\s*\n?)+)',       # Data rows
        re.MULTILINE,
    )

    matches = list(table_pattern.finditer(md_text))
    print(f"Detected {len(matches)} markdown table(s) in markdown output.")

    for idx, match in enumerate(matches, start=1):
        table_block = match.group(0)
        title_match = re.match(r'^\*\*(.*?)\*\*', table_block)
        title = title_match.group(1).strip() if title_match else None

        lines = [
            line.strip()
            for line in table_block.splitlines()
            if line.strip().startswith("|")
        ]
        if len(lines) < 2:
            continue

        header = [cell.strip() for cell in lines[0].strip("|").split("|")]
        n_cols = len(header)
        rows = []
        for row in lines[2:]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            # Correction : ajuste le nombre de colonnes
            if len(cells) < n_cols:
                # complète avec des cases vides
                cells += [""] * (n_cols - len(cells))
            elif len(cells) > n_cols:
                # tronque les colonnes en trop
                cells = cells[:n_cols]
            rows.append(cells)

        df = pd.DataFrame(rows, columns=header)
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue

        csv_path = out_dir / f"{stem}-md-table-{idx}.csv"
        html_path = out_dir / f"{stem}-md-table-{idx}.html"
        df.to_csv(csv_path, index=False, encoding="utf-8")
        with html_path.open("w", encoding="utf-8") as fh:
            if title:
                fh.write(f"<b>{title}</b>\n")
            fh.write(df.to_html(index=False))
        print(f"Saved markdown table {idx} → {csv_path}")

def export_full_document_to_csv(conv_res, out_path: Path):
   
    # Exporte tout le document de manière structurée dans un CSV, 
    # avec une ligne par item (titre, paragraphe, table, image…),
   
    from docling.datamodel.document import (
        SectionHeaderItem,
        TextItem,
        TableItem,
        PictureItem,
        ListItem,
    )

    rows = []
    doc = conv_res.document

    for item, level in doc.iterate_items():
        try:
            if isinstance(item, SectionHeaderItem):
                rows.append({
                    "type": f"heading_{level}",
                    "content": item.text,
                    "metadata": {"level": level, "label": str(item.label)},
                    "embedding": None,
                })
            elif isinstance(item, ListItem):
                rows.append({
                    "type": "list_item",
                    "content": item.text,
                    "metadata": {"label": str(item.label)},
                    "embedding": None,
                })
            elif isinstance(item, TextItem):
                rows.append({
                    "type": "paragraph",
                    "content": item.text,
                    "metadata": {"label": str(item.label)},
                    "embedding": None,
                })
            elif isinstance(item, TableItem):
                try:
                    content = item.export_to_markdown(doc=doc)
                except Exception:
                    content = ""
                rows.append({
                    "type": "table",
                    "content": content,
                    "metadata": {"label": str(item.label)},
                    "embedding": None,
                })
            elif isinstance(item, PictureItem):
                caption = ""
                try:
                    if item.captions:
                        caption = " ".join(
                            c.text for c in item.captions if hasattr(c, "text")
                        )
                except Exception:
                    pass
                rows.append({
                    "type": "picture",
                    "content": caption,
                    "metadata": {"label": str(item.label)},
                    "embedding": None,
                })
        except Exception as e:
            _log.warning("Skipped item during full export: %s", e)
            continue

    # fallback: full markdown if nothing was extracted
    if not rows:
        _log.warning("No structured items found, falling back to full markdown export.")
        rows.append({
            "type": "full_document",
            "content": doc.export_to_markdown(),
            "metadata": {},
            "embedding": None,
        })

    df = pd.DataFrame(rows, columns=[ "content", "metadata", "embedding"]) # ajouter "type", avant content
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")
    _log.info("Full document CSV: %s (%d rows)", out_path, len(df))
    print(f"Full document CSV: {out_path} ({len(df)} rows)")

# Main entry point, avec possibilité de passer le chemin du document à traiter et le sous-dossier de sortie en argument, 
# ou d'utiliser les valeurs hardcodées pour les tests

# def main(fp: str | Path | None = None, out_subdir: str | None = None):
#     logging.basicConfig(level=logging.INFO)

#     root_dir = Path(__file__).resolve().parent.parent
#     data_folder = root_dir / "data" / "input_files"

#     # document hardcodé pour les tests, ou chemin passé en argument
#     if fp is None:
#         input_doc_path = data_folder / "Domicilié dans les DOM-TOM, UE.pdf"  # CHANGER SELON LES TESTS
#     else:
#         p = Path(fp)
#         input_doc_path = p if p.is_absolute() else data_folder / fp

#     if not input_doc_path.exists():
#         raise FileNotFoundError(f"Input file not found: {input_doc_path}")

#     # Chemin vers le dépot des documents du test, à changer selon les tests, ou passé en argument
#     # sub = out_subdir or "test2"  # CHANGER LE NOM DU SUBDIR SELON LES TESTS
#     output_dir = root_dir / "data" / "output_files" / "tables" / "test4"
#     output_dir.mkdir(parents=True, exist_ok=True)

#     start_time = time.time()

#     # Utilise les paramètres du converter défini au niveau du module, qui incluent le VLM pour l'extraction de tables et la description d'images
#     conv_res = converter.convert(input_doc_path)
#     md = conv_res.document.export_to_markdown()

#     # Appel de la fonction d'export de tout le document dans un CSV structuré
#     export_full_document_to_csv(
#         conv_res,
#         root_dir / "data" / "output_files" / f"{input_doc_path.stem}.csv",
#     )

#     # Appel de la fonction d'export des tables détectées par docling, avec les options VLM pour l'extraction de tables
#     export_tables_from_conv(conv_res, output_dir)

#     # Appel la fonction si on veut aussi capter les tables présentes dans le markdown et pas détectées par docling
#     export_markdown_tables_from_markdown(md, output_dir, input_doc_path.stem)

#     end_time = time.time() - start_time
#     _log.info("Done in %.2f seconds.", end_time)


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Extract tables from PDF using docling VLM pipeline.")
#     parser.add_argument("--file", "-f", default=None, help="input filename or absolute path")
#     parser.add_argument("--out",  "-o", default="test1", help="output subfolder under data/output_files/tables/")
#     args = parser.parse_args()
#     main(args.file, args.out)

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)

    # Vérifie la project root :
    fp = "Adhésion traitement.pdf"  # CHANGER SELON LES TESTS
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

    INPUT_PATH  = PROJECT_ROOT / "data" / "input_files"
    OUTPUT_PATH = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / "Adhésion traitement" # CHANGER SELON LES TESTS (stage2_test/test1, test2, test3…)
    TABLES_PATH = OUTPUT_PATH / f"test1_{fp}" # CHANGER SELON LES TESTS (test1, test2, test3…)

    # check si les dossiers de sortie existent, sinon les créer
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    TABLES_PATH.mkdir(parents=True, exist_ok=True)



    ##### contrôle optionnel #####
    if not (INPUT_PATH / fp).exists():
        raise FileNotFoundError(f"Input file {INPUT_PATH / fp} does not exist.")

    # run extraction pipeline (utilise le converter VLM défini au niveau module)
    start_time = time.time()
    result = converter.convert(INPUT_PATH / fp)

    # calculé UNE SEULE FOIS et réutilisé partout
    md = result.document.export_to_markdown()
    # ── 1. Export markdown brut → CSV (même format que pipeline_base.py) ──────
    # data = [
    #     {
    #         "content": result.document.export_to_markdown(),
    #         "metadata": {},
    #         "embedding": None,
    #     }
    # ]

    # pd.DataFrame(data).to_csv(
    #     OUTPUT_PATH / "af_test4.csv",  # CHANGER LE NOM SELON LES TESTS
    #     encoding="utf-8", sep=",", index=None
    # )

    # Export markdown brut → CSV 
    pd.DataFrame([{"content": md, "metadata": {}, "embedding": None}]).to_csv(
        OUTPUT_PATH / "af_test1.csv", # CHANGER LE NOM SELON LES TESTS
        encoding="utf-8", sep=",", index=None
    )

    # Export structuré complet (titres, paragraphes, tables, images…)
    export_full_document_to_csv(
        result,
        OUTPUT_PATH / f"{Path(fp).stem}_structured.csv"
    )

    # Export tables docling + LaTeX fallback → CSV/HTML
    export_tables_from_conv(result, TABLES_PATH)

    # Export tables markdown → CSV/HTML
    export_markdown_tables_from_markdown(md, TABLES_PATH, Path(fp).stem)

    # vue console du markdown extrait
    print(result.document.export_to_markdown())

    end_time = time.time() - start_time
    _log.info("Done in %.2f seconds.", end_time)
