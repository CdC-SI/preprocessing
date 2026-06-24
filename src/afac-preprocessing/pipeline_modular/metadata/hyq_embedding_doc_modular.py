"""
Stage 5 - Script qui génère les embeddings des questions hyq pour un document donné.
Script 4 : hyq_embedding_doc_modular.py

Lit hyq.json depuis stage5 (écrit par metadata_generation_modular.py / step 11),
génère l'embedding de chaque question et écrit un CSV dédié par question :
    stage5/<doc_name>/hyq_<doc_name>/question_1.csv
    stage5/<doc_name>/hyq_<doc_name>/question_2.csv
    ...

Chaque CSV contient une ligne (+ en-tête) :
    CONTENT  : la question hyq
    METADATA : JSON avec le titre du document source, ex. {"title": "monDoc.pdf"}
    EMBEDDING: le vecteur d'embedding de la question, ex. "0.48, 0.84, 1.59, -2.71"

Usage:
    uv run python hyq_embedding_doc_modular.py --dotenv .env.test
    uv run python hyq_embedding_doc_modular.py --dotenv .env.test --doc-title "MonDoc.pdf"
    uv run python hyq_embedding_doc_modular.py --doc-name "MonDoc" --doc-title "MonDoc.pdf" --stage5 ./data/output_files/stage5_test
"""

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from openai import OpenAI

from utils.config import load_vlm_config
from utils.paths import project_root, resolve_doc_name

DEFAULT_STAGE5 = project_root() / "data" / "output_files"

_log = logging.getLogger(__name__)


def load_hyq(stage5_dir: Path, doc_name: str) -> list[str]:
    """
    Lit le fichier hyq.json du document depuis stage5.

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Liste de questions hyq
    :rtype: list[str]
    """
    hyq_path = stage5_dir / doc_name / "metadata" / "hyq.json"
    if not hyq_path.exists():
        raise FileNotFoundError(f"hyq.json introuvable pour '{doc_name}' dans {stage5_dir}")
    return json.loads(hyq_path.read_text(encoding="utf-8"))


def generate_embedding(text: str, client: OpenAI, embedding_model_name: str) -> list[float]:
    """
    Envoie le texte au modèle d'embedding et retourne le vecteur brut.

    :param text: Texte à encoder
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
    Convertit un vecteur d'embedding en chaîne sans crochets.
    Ex: [0.4, 0.8, 1.5] -> "0.4, 0.8, 1.5"

    :param embedding: Vecteur d'embedding
    :type embedding: list[float]
    :return: Représentation string du vecteur
    :rtype: str
    """
    return str(embedding).replace("[", "").replace("]", "")


def write_hyq_csv(
    stage5_dir: Path,
    doc_name: str,
    doc_title: str,
    questions: list[str],
    client: OpenAI,
    embedding_model_name: str,
) -> tuple[Path, int]:
    """
    Pour chaque question hyq, génère son embedding et écrit un CSV dédié :
    stage5/<doc_name>/hyq_<doc_name>/question_1.csv, question_2.csv, …

    Les erreurs par question sont loggées et ignorées — les questions suivantes
    sont toujours traitées.

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param doc_title: Titre du document source avec extension, ex. "monDoc.pdf"
    :type doc_title: str
    :param questions: Liste de questions hyq
    :type questions: list[str]
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param embedding_model_name: Nom du modèle d'embedding
    :type embedding_model_name: str
    :return: Tuple (chemin du dossier hyq créé, nombre de questions en erreur)
    :rtype: tuple[Path, int]
    """
    out_dir = stage5_dir / doc_name / "metadata" / f"hyq_{doc_name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    n_err = 0

    for i, question in enumerate(questions, start=1):
        _log.info("Embedding question %d/%d : %s...", i, len(questions), question[:60])
        try:
            embedding = generate_embedding(question, client, embedding_model_name)
            csv_path = out_dir / f"question_{i}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                writer.writerow(["CONTENT", "METADATA", "EMBEDDING"])
                writer.writerow([
                    question,
                    json.dumps({"title": doc_title}, ensure_ascii=False),
                    embedding_to_string(embedding),
                ])
        except Exception:
            _log.exception("Erreur question %d/%d — ignorée.", i, len(questions))
            n_err += 1

    return out_dir, n_err


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère les embeddings des questions hyq d'un document et les écrit dans un CSV par question.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python hyq_embedding_doc_modular.py --dotenv .env.test\n"
            "  uv run python hyq_embedding_doc_modular.py --dotenv .env.test --doc-title \"MonDoc.pdf\"\n"
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger (EMBEDDING_URL, VLM_CA_PEM, EMBEDDING_MODEL_NAME, DOC_NAME).",
    )
    parser.add_argument(
        "--doc-name",
        type=str,
        default=None,
        help="Nom du document sans extension. Si absent, résout DOC_NAME depuis --dotenv ou l'environnement.",
    )
    parser.add_argument(
        "--doc-title",
        type=str,
        default=None,
        help=(
            "Titre du fichier source avec extension (ex: \"MonDoc.pdf\"). "
            "Si absent, construit <DOC_NAME>.pdf."
        ),
    )
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

    doc_title = args.doc_title or f"{doc_name}.pdf"

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

    _log.info("Chargement des hyq pour : %s", doc_name)
    questions = load_hyq(args.stage5, doc_name)
    _log.info("%d question(s) trouvée(s)", len(questions))

    out_dir, n_err = write_hyq_csv(args.stage5, doc_name, doc_title, questions, client, embedding_model_name)

    n_ok = len(questions) - n_err
    _log.info("%d/%d fichier(s) CSV écrits dans : %s", n_ok, len(questions), out_dir)
    if n_err:
        _log.warning("%d question(s) en erreur.", n_err)
    sys.exit(0)


if __name__ == "__main__":
    main()
