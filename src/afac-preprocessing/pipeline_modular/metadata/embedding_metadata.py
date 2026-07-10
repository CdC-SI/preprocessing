"""
Script qui génère l'embedding du markdown final pour chaque document, et écrit le résultat dans le dossier de sortie.
Script 3 : embedding_metadata.py
Génère l'embedding du contenu markdown (markdown_dir) de chaque document via un modèle d'embedding,
écrit le vecteur dans output_dir/<doc_name>/embedding.json, et retourne le vecteur
sous forme de chaîne CSV (ex: "0.4, 0.8, 1.5") pour la colonne EMBEDDING du CSV final.

Output (output_dir/<doc_name>/):
    embedding.json  - vecteur brut (list[float])

Usage:
    uv run python embedding_metadata.py --dotenv .env.test --doc-name "MonDoc"
    uv run python embedding_metadata.py --doc-name "MonDoc" --markdown-dir ./data/output_files_preprocessing/markdown_test --output-dir ./data/output_files_preprocessing/output_test
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from openai import OpenAI

from utils.paths import project_root, resolve_doc_name
from utils.vlm_client import build_embedding_client, build_vlm_config, embedding_to_string, get_embedding

DEFAULT_OUTPUT_FILES = project_root() / "data" / "output_files_preprocessing"
DEFAULT_MARKDOWN_DIR = DEFAULT_OUTPUT_FILES
DEFAULT_OUTPUT_DIR = DEFAULT_OUTPUT_FILES

_log = logging.getLogger(__name__)


def _read_markdown(markdown_dir: Path, doc_name: str) -> str:
    """
    Lit le markdown final pour un document donné.

    Préfère <doc>_final_embed.md (tables Markdown remplacées par du JSONL, produit par
    markdown_tables_to_jsonl.py --embed-output) s'il existe, sinon <doc>_final.md.
    Rétrocompatible : les documents sans _final_embed.md (v1/v2/baseline) sont inchangés.

    :param markdown_dir: Dossier contenant le markdown final
    :type markdown_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Contenu markdown du document
    :rtype: str
    """
    for suffix in ("_final_embed.md", "_final.md"):
        candidate = markdown_dir / doc_name / f"{doc_name}{suffix}"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def generate_embedding(text: str, client: OpenAI, embedding_model_name: str) -> list[float]:
    """
    Envoie le texte au modèle d'embedding et retourne le vecteur brut.

    Délègue à utils.vlm_client.get_embedding (cache-first) — conservé sous ce nom car
    single_retrieval_nopreprocessing/single_docling_baseline.py l'importe directement.

    :param text: Contenu markdown du document (markdown_dir)
    :type text: str
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param embedding_model_name: Nom du modèle d'embedding
    :type embedding_model_name: str
    :return: Vecteur d'embedding
    :rtype: list[float]
    """
    return get_embedding(client, embedding_model_name, text)


def write_embedding_output(output_dir: Path, doc_name: str, embedding: list[float]) -> Path:
    """
    Écrit le vecteur brut dans output_dir/<doc_name>/metadata/embedding.json.

    :param output_dir: Dossier racine de sortie (metadata)
    :type output_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param embedding: Vecteur d'embedding
    :type embedding: list[float]
    :return: Chemin du dossier créé
    :rtype: Path
    """
    out_dir = output_dir / doc_name / "metadata"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "embedding.json").write_text(
        json.dumps(embedding, ensure_ascii=False), encoding="utf-8"
    )
    return out_dir


def run_embedding(
    doc_name: str,
    markdown_dir: Path,
    output_dir: Path,
    dotenv_path: Path | None = None,
) -> tuple[str, str]:
    """
    Lit le markdown depuis markdown_dir, génère l'embedding, écrit embedding.json dans output_dir,
    et retourne (embedding_string, embedding_model_name).
    La config est chargée ici (pas au niveau module) pour respecter le --dotenv tardif.

    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param markdown_dir: Dossier contenant le markdown final
    :type markdown_dir: Path
    :param output_dir: Dossier racine de sortie (metadata)
    :type output_dir: Path
    :param dotenv_path: Fichier .env à charger pour la config embedding
    :type dotenv_path: Path | None
    :return: Tuple (vecteur string CSV, nom du modèle embedding)
    :rtype: tuple[str, str]
    """
    vlm_cfg = build_vlm_config(dotenv_path=dotenv_path)
    embedding_model_name = vlm_cfg.embedding_model_name
    client = build_embedding_client(vlm_cfg)

    markdown_content = _read_markdown(markdown_dir, doc_name)
    if not markdown_content:
        raise FileNotFoundError(
            f"Aucun fichier markdown trouvé pour '{doc_name}' dans {markdown_dir}"
        )

    _log.info("Génération de l'embedding")
    embedding = generate_embedding(markdown_content, client, embedding_model_name)

    out_dir = write_embedding_output(output_dir, doc_name, embedding)
    _log.info("embedding écrit dans : %s", out_dir / "embedding.json")

    return embedding_to_string(embedding), embedding_model_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère l'embedding du markdown final et écrit le résultat dans output_dir."
    )
    parser.add_argument(
        "--doc-name",
        type=str,
        default=None,
        help="Nom du document sans extension. Si absent, résout DOC_NAME depuis --dotenv ou l'environnement.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger (EMBEDDING_URL, VLM_CA_PEM, EMBEDDING_MODEL_NAME, DOC_NAME).",
    )
    parser.add_argument(
        "--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR,
        help="Dossier contenant le markdown final à embedder.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Dossier racine de sortie (embedding.json).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation. Défaut : INFO.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    doc_name = resolve_doc_name(args, primary_flag="--doc-name")
    dotenv_path = args.dotenv

    _log.info("Embedding de : %s", doc_name)
    embedding_str, model_name = run_embedding(doc_name, args.markdown_dir, args.output_dir, dotenv_path=dotenv_path)

    _log.info("modèle : %s", model_name)
    preview = ", ".join(embedding_str.split(", ")[:5])
    _log.info("embedding (5 premières valeurs) : %s, ...", preview)
    sys.exit(0)


if __name__ == "__main__":
    main()
