"""
Stage 5 - Script de génération de metadata pour chaque document
Script 1 : metadata_generation.py

Creation des metadata personnalisees pour chaque document de la base de données
Ces metadata seront utilisées pour le reranking et l'embedding
Elles contiennent des informations sur le document (voir ci-dessous)

Exemple de structure de metadata pour un document :

### Restructuration du document final :
- id : uuid
- user_uuid : empty string default
- source : str -> "afac" ne change pas
- title : str
- doctype : pdf, word, html, ...
- version : voir la metadata du document ex : "Version": "4.2", extrait du document lui même
- visibility : "internal" / "public" / "sensitive" default "internal"
- language : langue du document, ex : "fr" pour français pour tous AF 
- outgoing_links : List[Refs] représente les docuemnt qui se réfèrent à ce document, ex : doc://123
- incoming_links : List[Refs] représente les documents qui sont référencés par ce document, ex : doc://456
- created_at : ISO 8601 format YYYY-MM-DDTHH:MM:SSZ
- updated_at : ISO 8601 format YYYY-MM-DDTHH:MM:SSZ
- media_type : path des images qui sont extraites du document, ex : "media_type": ["image1.png", "image2.png"]
- parent_label : List[str] # Dossier parent du document, ici AF (Assurance Facultative)
- children_label : List[str] # Dossier enfant du document ex : AF (Assurance Facultative) > Sinistre > Dossier Sinistre
- sibling : List[str] # Affiche tous les documents du même niveau hiérarchique, ex : monexemple.pdf = assurance_facultative.pdf = assurance_obligatoire.pdf, etc .. qui ne sont pas le document lui même
- content : str # fichier markdown généré par le pipeline d'extraction (ex : Détachement.md)
- page_count : int # nombre de pages du document
- chunk_count : int # nombre de chunks générés par le pipeline d'extraction
- embedding_model : str # nom du modèle d'embedding utilisé pour générer les embeddings, ex : "text-embedding-3-small"
- indexed_at : ISO 8601 format YYYY-MM-DDTHH:MM:SSZ # date d'indexation du document dans la base de données, ex : "2024-03-12T10:12:00Z"
- page_num: str représente
- resume : str # résumé du document généré par un modèle de langage, à partir du markdown final (stage4), ex : "Ce document traite des conditions générales d'assurance facultative pour les véhicules, incluant les garanties, exclusions et procédures de réclamation."
- intent : List[str] # intention du document générée par un modèle de langage, à partir du markdown final (stage4), ex : "Le document a pour but d'informer les assurés des conditions générales de leur contrat d'assurance facultative, afin de clarifier les garanties offertes et les démarches à suivre en cas de sinistre."
- hyq : List[str] # questions fréquentes générées par un modèle de langage, à partir du markdown final (stage4), ex : "Quelles sont les garanties incluses dans l'assurance facultative ? Comment faire une réclamation en cas de sinistre ? Y a-t-il des exclusions de garantie à connaître ?"

Le document final est un CSV de 3 colones avec :
CONTENT (markdown généré par le pipeline d'extraction) | METADATA json (metadata généré par ce script) | EMBEDDING (embedding généré par le pipeline d'embedding)

Génère les metadata pour un document et écrit une ligne dans le CSV final.

En sortie il me faut: 
Colonnes CSV :
    CONTENT  (markdown du fichier généré. Rempli par le pipeline d'extraction)
    METADATA (JSON - généré par ce script)
    EMBEDDING (vide - non encore implémenté)

Usage :
    python metadata.py "Taxation/Taxation E+N au prorata.pdf"
    python metadata.py "Taxation/DISPENSE/Annulation d'une dispense.pdf" --output ./out/docs.csv
"""
import argparse
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config import load_vlm_config
from enhancement_metadata import run_enhancement
from embedding_metadata import run_embedding

# Root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FOLDER_SOURCE  = Path(__file__).resolve().parent / "folder_source"
DEFAULT_STAGE1 = PROJECT_ROOT / "data" / "output_files" / "stage1_test"
DEFAULT_STAGE2 = PROJECT_ROOT / "data" / "output_files" / "stage2_test"
DEFAULT_STAGE3 = PROJECT_ROOT / "data" / "output_files" / "stage3_test"
DEFAULT_STAGE4 = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
DEFAULT_STAGE5 = PROJECT_ROOT / "data" / "output_files" / "stage5_test"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "output_files" / "metadata" / "documents.csv"

_config = load_vlm_config()
EMBEDDING_MODEL_NAME = _config["EMBEDDING_MODEL_NAME"]

VISIBILITY_DEFAULT = "internal"
ISO_8601_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Hiérarchie (folder_source)

def get_hierarchy(folder_source: Path, relative_doc_path: str) -> dict:
    """
    Déduit source, parent_label, children_label et sibling
    depuis le chemin relatif dans folder_source.

    Exemple : "Taxation/DISPENSE/Annulation d'une dispense.pdf"
        source -> "afac"
        parent_label -> ["DISPENSE"]
        children_label -> sous-dossiers présents dans DISPENSE (dossiers frères du document)
        sibling -> autres fichiers présents dans DISPENSE (fichiers frères du document)
    """
    doc_path = Path(relative_doc_path)
    source = "afac" # Documents AFAC ne change pas ici
    parts = doc_path.parts # ("Taxation", "DISPENSE", "fichier.pdf")

    # Dossier parent direct du document
    parent_dir = (folder_source / doc_path).parent

    parent_label = [parts[-2]] if len(parts) >= 2 else [] # dossier immédiat

    # Trouver les dossiers au même niveau que le document courant
    children_label = []
    if parent_dir.exists():
        children_label = sorted(
            directory.name for directory in parent_dir.iterdir()
            if directory.is_dir()
            )
    
    # Trouve tous les autres documents du même niveau hiérarchique (même dossier parent) pour remplir "sibling"
    siblings = []
    if parent_dir.exists():
        siblings = sorted(
            fichier.name for fichier in parent_dir.iterdir() 
            if fichier.is_file() and fichier.name != doc_path.name # and fichier.suffix.lower() == ".pdf" si on nen veut que les pdf, on retire le docuement lui-même
        )

    return {
        "source": source, # dossier racine
        "parent_label": parent_label, # dossier immédiat
        "children_label": children_label, # tous les dossiers dans le même niveau hiérarchique (même dossier parent)
        "sibling": siblings, # documents du même niveau hiérarchique (même dossier parent)
    }


# Stage 1 – JSON Docling
def load_stage1_json(stage1_dir: Path, doc_name: str) -> dict:
    """
    Docstring for load_stage1_json
    
    :param stage1_dir: Description
    :type stage1_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: dict
    """
    path = stage1_dir / doc_name / f"{doc_name}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_doctype(doc_json: dict) -> str:
    """
    Docstring for get_doctype
    - Mimetype mapping pour déterminer le type de document à partir du champ "origin.mimetype" dans doc_json.
    - Si le mimetype n'est pas dans le mapping, retourne la partie après "/" du mimetype ou "unknown" si le format est inattendu.

    :param doc_json: Description
    :type doc_json: dict
    :return: Description
    :rtype: str
    """
    mimetype = doc_json.get("origin", {}).get("mimetype", "")
    mapping = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/msword": "doc",
        "text/html": "html",
        "text/plain": "txt",
        "image/png": "png",
        "image/jpeg": "jpg",
    }
    return mapping.get(mimetype, mimetype.split("/")[-1] if "/" in mimetype else "unknown") # [-1] pour prendre la partie après "/" ou "unknown" si le format est inattendu


def get_page_count(doc_json: dict) -> int:
    """
    Docstring for get_page_count
    
    :param doc_json: Description
    :type doc_json: dict
    :return: Description
    :rtype: int
    """
    return len(doc_json.get("pages", {}))


def find_version_table(doc_json: dict):
    """
    Docstring for find_version_table
    Retourne (cells, headers) de la première table ayant une colonne 'Version'.

    :param doc_json: Description
    :type doc_json: dict
    """
    for table in doc_json.get("tables", []):
        cells = table.get("data", {}).get("table_cells", [])
        headers = [c["text"].strip().lower() for c in cells if c.get("column_header")]
        if "version" in headers:
            return cells, headers
    return None, None


def get_version(doc_json: dict) -> str:
    """
    Docstring for get_version
    
    :param doc_json: Description
    :type doc_json: dict
    :return: Description
    :rtype: str
    """
    cells, _ = find_version_table(doc_json)
    if not cells:
        return ""
    version_col = next(
        (c["start_col_offset_idx"] for c in cells
         if c.get("column_header") and c["text"].strip().lower() == "version"),
        None,
    )
    if version_col is None:
        return ""
    versions = [
        c["text"].strip() for c in cells
        if not c.get("column_header")
        and c["start_col_offset_idx"] == version_col
        and c["text"].strip()
    ]
    return versions[-1] if versions else ""


def parse_date(raw: str) -> str:
    """
    Docstring for parse_date
    - Tente de parser une date à partir de formats courants et retourne en ISO 8601.
    - Si aucun format ne correspond, retourne une chaîne vide.

    :param raw: Description
    :type raw: str
    :return: Description
    :rtype: str
    """
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime(ISO_8601_FMT)
        except ValueError:
            continue
    return ""


# Stage 2 – images extraites
def get_images(stage2_dir: Path, doc_name: str) -> list[str]:
    """
    Docstring for get_images
    - Retourne la liste des images extraites du document, triée par ordre alphabétique.
    - Les images sont attendues dans stage2_dir/doc_name/used_images/
    - Seules les extensions .png, .jpg, .jpeg sont prises en compte.

    :param stage2_dir: Description
    :type stage2_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: list[str]
    """
    img_dir = stage2_dir / doc_name / "used_images"
    if not img_dir.exists():
        return []
    return sorted(
        f.name for f in img_dir.iterdir()
        if f.suffix.lower() in (".png", ".jpg", ".jpeg")
    )


# Stage 3 – hyperlinks
def read_jsonl(path: Path) -> list[dict]:
    """
    Docstring for read_jsonl
    - Lit un fichier JSONL et retourne une liste de dictionnaires.
    - Ignore les lignes vides.

    :param path: Description
    :type path: Path
    :return: Description
    :rtype: list[dict]
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def get_outgoing_links(stage3_dir: Path, doc_name: str) -> list[dict]:
    """
    Docstring for get_outgoing_links
    - Retourne la liste des liens hypertextes extraits du document, avec leur texte d'ancrage et numéro de page.
    - Les liens sont attendus dans stage3_dir/doc_name/hyperlinks_data_doc_name.jsonl
    - Chaque objet dans le JSONL doit contenir "text", "hyperlink" et "page_number".

    :param stage3_dir: Description
    :type stage3_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: list[dict]
    """
    jsonl = stage3_dir / doc_name / f"hyperlinks_data_{doc_name}.jsonl"
    if not jsonl.exists():
        return []
    return [
        {
            "text": outgoing.get("text", ""),
            "url": outgoing.get("hyperlink", ""),
            "page": outgoing.get("page_number", 0),
        }
        for outgoing in read_jsonl(jsonl)
    ]


def get_incoming_links(stage3_dir: Path, doc_name: str) -> list[dict]:
    """
    Docstring for get_incoming_links
    - Parcourt les hyperlinks de tous les autres documents pour trouver des références à doc_name.

    :param stage3_dir: Description
    :type stage3_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: list[dict]
    """
    if not stage3_dir.exists():
        return []
    needle = doc_name.lower()
    incoming = []
    for other_dir in sorted(stage3_dir.iterdir()):
        if not other_dir.is_dir() or other_dir.name == doc_name:
            continue
        jsonl = other_dir / f"hyperlinks_data_{other_dir.name}.jsonl"
        if not jsonl.exists():
            continue
        for obj in read_jsonl(jsonl):
            if needle in obj.get("text", "").lower() or needle in obj.get("hyperlink", "").lower():
                incoming.append({
                    "from_doc": other_dir.name,
                    "text": obj.get("text", ""),
                    "url": obj.get("hyperlink", ""),
                })
    return incoming


# Stage 4 – markdown final
def get_stage4_info(stage4_dir: Path, doc_name: str) -> tuple[str, int]:
    """
    Docstring for get_stage4_info
    - Retourne (content_filename, chunk_count).
    - Retourne (content_filename, chunk_count).
    - Cherche stage4_dir/{doc_name}_vlm_check.md ; si présent chunk_count=1, sinon 0.

    :param stage4_dir: Description
    :type stage4_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: tuple[str, int]
    """
    if not stage4_dir.exists():
        return f"{doc_name}_vlm_check.md", 0

    # Fichier vlm_check généré par le pipeline
    single = stage4_dir / f"{doc_name}_vlm_check.md"
    if single.exists():
        return single.name, 1

    return f"{doc_name}_vlm_check.md", 0


def get_stage4_content(stage4_dir: Path, doc_name: str) -> str:
    """
    Docstring for get_stage4_content
    - Retourne le contenu markdown du document depuis stage4 (fichier unique ou chunks concaténés).

    :param stage4_dir: Description
    :type stage4_dir: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: str
    """
    single = stage4_dir / f"{doc_name}_vlm_check.md" # Modifier selon le besoin
    if single.exists():
        return single.read_text(encoding="utf-8")
    return ""


def get_pdf_creation_date(pdf_path: Path) -> str:
    """
    Docstring for get_pdf_creation_date
    - Lit la date de création du PDF via fitz (PyMuPDF) et retourne en ISO 8601.

    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: str
    """
    try:
        with fitz.open(str(pdf_path)) as doc:
            raw = doc.metadata.get("creationDate", "")
        if not raw:
            return ""
        # Format fitz : D:YYYYMMDDHHmmSS[+HH'mm'] ou D:YYYYMMDDHHmmSSZ
        raw = raw.lstrip("D:").replace("'", "").split("+")[0].split("-")[0].rstrip("Z")
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").strftime(ISO_8601_FMT)
    except Exception:
        return ""


def get_page_num(page_count: int) -> str:
    """Retourne les numéros de page sous forme de chaîne CSV. Ex: 3 pages -> '1,2,3'."""
    if page_count <= 0:
        return ""
    return ",".join(str(i) for i in range(1, page_count + 1))


# Construction du bloc metadata
def build_metadata(
    relative_doc_path: str,
    folder_source: Path,
    stage1_dir: Path,
    stage2_dir: Path,
    stage3_dir: Path,
    stage4_dir: Path,
) -> dict:
    """
    Docstring for build_metadata
    - Construit le bloc de metadata pour un document donné, en combinant les informations extraites des différentes étapes du pipeline.

    :param relative_doc_path: Description
    :type relative_doc_path: str
    :param folder_source: Description
    :type folder_source: Path
    :param stage1_dir: Description
    :type stage1_dir: Path
    :param stage2_dir: Description
    :type stage2_dir: Path
    :param stage3_dir: Description
    :type stage3_dir: Path
    :param stage4_dir: Description
    :type stage4_dir: Path
    :return: Description
    :rtype: dict
    """
    doc_name = Path(relative_doc_path).stem
    doc_name_extension = Path(relative_doc_path).name
    hierarchy = get_hierarchy(folder_source, relative_doc_path)
    doc_json = load_stage1_json(stage1_dir, doc_name) # charge le JSON généré à l'étape 1 pour extraire doctype, version, page_count, etc.
    content_file, chunk_count = get_stage4_info(stage4_dir, doc_name)
    created_at = get_pdf_creation_date(folder_source / relative_doc_path)
    updated_at = datetime.now(timezone.utc).strftime(ISO_8601_FMT)
    page_count = get_page_count(doc_json)

    return {
        "id": str(uuid.uuid4()),
        "user_uuid": "", # à remplir par le pipeline d'indexation si besoin
        "source": hierarchy["source"],
        "title": doc_name_extension,
        "doctype": get_doctype(doc_json),
        "version": get_version(doc_json),
        "visibility": VISIBILITY_DEFAULT, # "internal" par défaut, à ajuster si besoin
        "language": "fr", # fr par défaut pour tous les documents AF, à ajuster si besoin
        "outgoing_links": get_outgoing_links(stage3_dir, doc_name),
        "incoming_links": get_incoming_links(stage3_dir, doc_name),
        "created_at": created_at, # date de création du PDF ou chaîne vide si non trouvée
        "updated_at": updated_at, # date d'exécution du script
        "media_type": get_images(stage2_dir, doc_name),
        "parent_label": hierarchy["parent_label"],
        "children_label": hierarchy["children_label"],
        "sibling": hierarchy["sibling"],
        "content": content_file,
        "page_count": page_count,
        "page_num": get_page_num(page_count),
        "chunk_count": chunk_count,
        "embedding_model": EMBEDDING_MODEL_NAME,
    }

# Écriture CSV


def write_csv_row(output_path: Path, metadata: dict, content: str = "", embedding: str = "") -> None:
    """
    Docstring for write_csv_row
    - Écrit une ligne dans le CSV final avec les colonnes CONTENT (markdown), METADATA (JSON) et EMBEDDING.
    - Si le fichier n'existe pas, crée le fichier et écrit l'en-tête avant la ligne de données.
    - Si le fichier existe déjà, ajoute simplement la ligne de données sans réécrire l'en-tête.

    :param output_path: Description
    :type output_path: Path
    :param metadata: Description
    :type metadata: dict
    :param content: Contenu markdown du document (stage4)
    :type content: str
    :param embedding: Vecteur d'embedding sous forme de chaîne CSV, ex. "0.4, 0.8, 1.5"
    :type embedding: str
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_path.exists()
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        if write_header:
            writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
        writer.writerow([
            content,                                   # CONTENT  – markdown stage4
            json.dumps(metadata, ensure_ascii=False),  # METADATA – ce script
            embedding,                                 # EMBEDDING – vecteur string "0.4, 0.8, 1.5"
        ])

# Chargement des informations du pipeline et exécution


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère les metadata d'un document et les écrit dans le CSV final."
    )
    parser.add_argument(
        "doc_path",
        help='Chemin relatif dans folder_source. Ex: "Taxation/DISPENSE/Annulation d\'une dispense.pdf"',
    )
    parser.add_argument(
        "--folder-source", type=Path, default=FOLDER_SOURCE,
        help="Racine de la hiérarchie documentaire",
    )
    parser.add_argument("--stage1", type=Path, default=DEFAULT_STAGE1)
    parser.add_argument("--stage2", type=Path, default=DEFAULT_STAGE2)
    parser.add_argument("--stage3", type=Path, default=DEFAULT_STAGE3)
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Fichier CSV de sortie (default: <doc_name>_final.csv dans le dossier metadata)",
    )
    args = parser.parse_args()

    doc_name = Path(args.doc_path).stem
    output_path = args.output or (DEFAULT_OUTPUT.parent / f"{doc_name}_final.csv")

    metadata = build_metadata(
        args.doc_path,
        folder_source=args.folder_source,
        stage1_dir=args.stage1,
        stage2_dir=args.stage2,
        stage3_dir=args.stage3,
        stage4_dir=args.stage4,
    )

    # Stage 5 – VLM enrichment (resume, intent, hyq) + write stage5 files
    enrichment = run_enhancement(doc_name, args.stage4, args.stage5)
    metadata["resume"] = enrichment["resume"]
    metadata["intent"] = enrichment["intent"]
    metadata["hyq"] = enrichment["hyq"]

    # Stage 5 – embedding du contenu markdown (stage4)
    embedding_str = run_embedding(doc_name, args.stage4, args.stage5)

    content = get_stage4_content(args.stage4, doc_name)
    write_csv_row(output_path, metadata, content, embedding_str)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"\n→ Ligne ajoutée dans : {output_path}")


if __name__ == "__main__":
    main()
