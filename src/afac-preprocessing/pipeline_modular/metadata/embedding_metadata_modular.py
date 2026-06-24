"""
Stage 5 - Script qui génère l'embedding du markdown stage4 pour chaque document, et écrit le résultat dans stage5.
Script 3 : embedding_metadata_modular.py
Génère l'embedding du contenu markdown (stage 4) de chaque document via un modèle d'embedding,
écrit le vecteur dans stage5/<doc_name>/embedding.json, et retourne le vecteur
sous forme de chaîne CSV (ex: "0.4, 0.8, 1.5") pour la colonne EMBEDDING du CSV final.

Output (stage5/<doc_name>/):
    embedding.json  - vecteur brut (list[float])

Usage:
    uv run python embedding_metadata_modular.py --dotenv .env.test --doc-name "MonDoc"
    uv run python embedding_metadata_modular.py --doc-name "MonDoc" --stage4 ./data/output_files/stage4_test --stage5 ./data/output_files/stage5_test
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from openai import OpenAI

from utils.config import load_vlm_config
from utils.paths import project_root, resolve_doc_name

DEFAULT_OUTPUT_FILES = project_root() / "data" / "output_files"
DEFAULT_STAGE4 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE5 = DEFAULT_OUTPUT_FILES

_log = logging.getLogger(__name__)


def _read_stage4(stage4_dir: Path, doc_name: str) -> str:
    """
    Lit le markdown du stage 4 pour un document donné.

    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Contenu markdown du document
    :rtype: str
    """
    single = stage4_dir / doc_name / f"{doc_name}_final.md"
    if single.exists():
        return single.read_text(encoding="utf-8")
    return ""


def generate_embedding(text: str, client: OpenAI, embedding_model_name: str) -> list[float]:
    """
    Envoie le texte au modèle d'embedding et retourne le vecteur brut.

    :param text: Contenu markdown du document (stage 4)
    :type text: str
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param embedding_model_name: Nom du modèle d'embedding
    :type embedding_model_name: str
    :return: Vecteur d'embedding
    :rtype: list[float]
    """
    response = client.embeddings.create(
        input=text,
        model=embedding_model_name,
    )
    return response.data[0].embedding


def embedding_to_string(embedding: list[float]) -> str:
    """
    Convertit un vecteur d'embedding en chaîne.
    Ex: [0.4, 0.8, 1.5] -> "0.4, 0.8, 1.5"

    :param embedding: Vecteur d'embedding
    :type embedding: list[float]
    :return: Représentation string du vecteur sans crochets
    :rtype: str
    """
    return str(embedding).replace("[", "").replace("]", "")


def write_stage5_embedding(stage5_dir: Path, doc_name: str, embedding: list[float]) -> Path:
    """
    Écrit le vecteur brut dans stage5/<doc_name>/metadata/embedding.json.

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param embedding: Vecteur d'embedding
    :type embedding: list[float]
    :return: Chemin du dossier créé
    :rtype: Path
    """
    out_dir = stage5_dir / doc_name / "metadata"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "embedding.json").write_text(
        json.dumps(embedding, ensure_ascii=False), encoding="utf-8"
    )
    return out_dir


def run_embedding(
    doc_name: str,
    stage4_dir: Path,
    stage5_dir: Path,
    dotenv_path: Path | None = None,
) -> tuple[str, str]:
    """
    Lit le markdown stage4, génère l'embedding, écrit embedding.json en stage5,
    et retourne (embedding_string, embedding_model_name).
    La config est chargée ici (pas au niveau module) pour respecter le --dotenv tardif.

    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param stage5_dir: Dossier stage5
    :type stage5_dir: Path
    :param dotenv_path: Fichier .env à charger pour la config embedding
    :type dotenv_path: Path | None
    :return: Tuple (vecteur string CSV, nom du modèle embedding)
    :rtype: tuple[str, str]
    """
    config = load_vlm_config(dotenv_path=dotenv_path)
    ca_path = config["CA_PATH"]
    embedding_model_name = config["EMBEDDING_MODEL_NAME"]
    parsed = urlparse(config["EMBEDDING_URL"])
    base_url = urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", ""))
    client = OpenAI(
        base_url=base_url,
        api_key="no-key",
        http_client=httpx.Client(verify=ca_path),
    )

    markdown_content = _read_stage4(stage4_dir, doc_name)
    if not markdown_content:
        raise FileNotFoundError(
            f"Aucun fichier markdown trouvé pour '{doc_name}' dans {stage4_dir}"
        )

    _log.info("Génération de l'embedding")
    embedding = generate_embedding(markdown_content, client, embedding_model_name)

    out_dir = write_stage5_embedding(stage5_dir, doc_name, embedding)
    _log.info("embedding écrit dans : %s", out_dir / "embedding.json")

    return embedding_to_string(embedding), embedding_model_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère l'embedding du markdown stage4 et écrit le résultat dans stage5."
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
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
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
    embedding_str, model_name = run_embedding(doc_name, args.stage4, args.stage5, dotenv_path=dotenv_path)

    _log.info("modèle : %s", model_name)
    preview = ", ".join(embedding_str.split(", ")[:5])
    _log.info("embedding (5 premières valeurs) : %s, ...", preview)
    sys.exit(0)


if __name__ == "__main__":
    main()
