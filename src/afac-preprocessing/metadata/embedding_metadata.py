"""
Génère l'embedding du contenu markdown (stage 4) de chaque document via un modèle d'embedding,
écrit le vecteur dans stage5/<doc_name>/embedding.json, et retourne le vecteur
sous forme de chaîne CSV (ex: "0.4, 0.8, 1.5") pour la colonne EMBEDDING du CSV final.

Output (stage5/<doc_name>/):
    embedding.json  - vecteur brut (list[float])

Usage:
    python3 embedding_metadata.py "Annulation et retaxation"
    python3 embedding_metadata.py "Détachement" --stage4 ./data/output_files/stage4_test --stage5 ./data/output_files/stage5_test
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config import load_vlm_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGE4 = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
DEFAULT_STAGE5 = PROJECT_ROOT / "data" / "output_files" / "stage5_test"

_config = load_vlm_config()
CA_PATH = _config["CA_PATH"]
EMBEDDING_MODEL_NAME = _config["EMBEDDING_MODEL_NAME"]

_parsed = urlparse(_config["EMBEDDING_URL"])
_base_url = urlunparse((_parsed.scheme, _parsed.netloc, "/v1", "", "", ""))

client = OpenAI(
    base_url=_base_url,
    api_key="no-key",
    http_client=httpx.Client(verify=CA_PATH),
)


def _read_stage4(stage4_dir: Path, doc_name: str) -> str:
    """
    Lit le markdown du stage 4 pour un document donné. 
    Si un fichier unique existe, le lit. Sinon, lit tous les chunks et les concatène.
    
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :return: Contenu markdown du document
    :rtype: str
    """
    single = stage4_dir / f"{doc_name}.md"
    if single.exists():
        return single.read_text(encoding="utf-8")
    chunks = sorted(stage4_dir.glob(f"{doc_name}_*.md"))
    if chunks:
        return "\n\n".join(p.read_text(encoding="utf-8") for p in chunks)
    return ""


def generate_embedding(text: str) -> list[float]:
    """
    Envoie le texte au modèle d'embedding et retourne le vecteur brut.

    :param text: Contenu markdown du document (stage 4)
    :type text: str
    :return: Vecteur d'embedding
    :rtype: list[float]
    """
    response = client.embeddings.create(
        input=text,
        model=EMBEDDING_MODEL_NAME,
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
    Écrit le vecteur brut dans stage5/<doc_name>/embedding.json.

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param embedding: Vecteur d'embedding
    :type embedding: list[float]
    :return: Chemin du dossier créé
    :rtype: Path
    """
    out_dir = stage5_dir / doc_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "embedding.json").write_text(
        json.dumps(embedding, ensure_ascii=False), encoding="utf-8"
    )
    return out_dir


def run_embedding(doc_name: str, stage4_dir: Path, stage5_dir: Path) -> str:
    """
    Lit le markdown stage4, génère l'embedding, écrit embedding.json en stage5,
    et retourne le vecteur sous forme de chaîne CSV.

    :param doc_name: Nom du document sans extension
    :type doc_name: str
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param stage5_dir: Dossier stage5
    :type stage5_dir: Path
    :return: Vecteur d'embedding sous forme de chaîne CSV, ex. "0.4, 0.8, 1.5"
    :rtype: str
    """
    markdown_content = _read_stage4(stage4_dir, doc_name)
    if not markdown_content:
        raise FileNotFoundError(
            f"Aucun fichier markdown trouvé pour '{doc_name}' dans {stage4_dir}"
        )

    print("Génération de l'embedding :")
    embedding = generate_embedding(markdown_content)

    out_dir = write_stage5_embedding(stage5_dir, doc_name, embedding)
    print(f"OK embedding écrit dans : {out_dir / 'embedding.json'}")

    return embedding_to_string(embedding)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère l'embedding du markdown stage4 et écrit le résultat dans stage5."
    )
    parser.add_argument(
        "doc_name",
        help='Nom du document sans extension. Ex: "Annulation et retaxation"',
    )
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    args = parser.parse_args()

    print(f"\nEmbedding de : {args.doc_name}")
    embedding_str = run_embedding(args.doc_name, args.stage4, args.stage5)

    print("\n--- embedding (extrait, 5 premières valeurs) ---")
    preview = ", ".join(embedding_str.split(", ")[:5])
    print(f"  {preview}, ...")


if __name__ == "__main__":
    main()
