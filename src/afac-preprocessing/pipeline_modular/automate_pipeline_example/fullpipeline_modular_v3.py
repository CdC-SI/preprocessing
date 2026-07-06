"""Full pipeline v3 — sans conversion JSON des tables, avec descriptions d'images activées.

Diffère de fullpipeline_modular_v2.py :
  - Retire opencv_checker_modular.py (QA visuelle uniquement, ne produit rien en aval),
    csv_to_jsonlines_modular.py et load_jsonline_doctags_modular.py : les tables restent
    au format <otsl> natif Docling et sont converties en tables markdown normales par
    docling_markdown_converter_modular.py, au lieu du JSON verbeux "une ligne = un objet
    JSON avec toutes les clés de colonnes répétées" (mesuré sur "Liste pays UE-AELE" :
    373 → 1038 mots pour le même contenu, soit x2.8 de bruit dans l'embedding).
  - description_image_context_modular.py tourne avec --image-description (désactivé par
    défaut dans v2) : les captures d'écran/diagrammes du PDF sont désormais décrites au
    lieu d'être invisibles à la recherche sémantique.
  - url_tuning_vlm_modular.py et markdown_control_vlm_modular.py tournent avec
    --prompt-variant v3 : les prompts v2 supposent des tables JSON lines dans le
    contenu reçu (cf. prompts.py) — faux ici puisqu'on garde les tables natives.
  - Toutes les étapes écrivent sous --output-root (défaut : data/output_files_v3/) au
    lieu de data/output_files_preprocessing/, pour ne jamais écraser le pipeline v2 ni
    data/baseline_evaluation/ (docling brut). Permet de relancer v3 autant de fois que
    nécessaire et de comparer les 3 arbres de sortie côte à côte.
  - resume.md/intent.json/hyq.json + les embeddings HyQ déjà calculés
    (hyq_<doc>/question_N.csv) sont copiés tels quels depuis data/output_files_preprocessing/ vers
    --output-root avant les étapes 10/11, plutôt que régénérés : le texte des questions
    ne dépend pas du pipeline de prétraitement (même modèle, même texte → même embedding),
    les régénérer serait un gaspillage d'appels API et casserait la comparabilité établie
    avec la baseline docling et le pipeline v2 (mêmes questions des deux côtés).
  - L'étape 10 tourne avec --skip-enhancement : sans ce flag, metadata_generation_modular.py
    appelle en interne run_enhancement() et régénère resume/intent/hyq via VLM depuis le
    nouveau _final.md, écrasant le hyq.json seedé ci-dessus avec de NOUVELLES questions —
    ce qui cassait silencieusement la comparabilité avant ce correctif.
  - L'étape 09 (markdown_tables_to_jsonl_modular.py --embed-output) réécrit _final.md en
    _final_embed.md (tables Markdown → JSONL) AVANT l'embedding : embedding_metadata_modular
    et metadata_generation_modular préfèrent ce fichier s'il existe. Accepté au prix du
    surcoût de tokens déjà mesuré, pour que l'embedding porte sur des tables structurées.

Usage:
    uv run python fullpipeline_modular_v3.py --dotenv .env.test
    uv run python fullpipeline_modular_v3.py --input data/input_files/afac/Adhésion/MonDoc.pdf
    uv run python fullpipeline_modular_v3.py --dotenv .env.test --from-step 6
    uv run python fullpipeline_modular_v3.py --dotenv .env.test --skip-steps 3
    uv run python fullpipeline_modular_v3.py --dotenv .env.test --output-root data/output_files_v3_test

Étapes :
  01  pipeline_multietape_modular.py           # extraction Docling (doctags/json/md/txt)
  02  reordered_doctags_modular.py             # réordonnancement des blocs (y0/x0, par page)
  03  description_image_context_modular.py     # descriptions d'images VLM (activées)
  04  url_extaction_modular.py                 # extraction des liens hypertexte du PDF
  05  url_tuning_vlm_modular.py                # injection des liens dans le doctags via VLM (prompt v3)
  06  docling_markdown_converter_modular.py    # conversion doctags → markdown (page par page)
  07  markdown_control_vlm_modular.py          # correction VLM du markdown (prompt v3)
  08  inject_image_descriptions_modular.py     # injection descriptions images → _final.md
  09  markdown_tables_to_jsonl_modular.py      # tables → JSONL (traçabilité) + _final_embed.md (source de l'embedding)
  10  metadata_generation_modular.py           # metadata + embedding CSV (lit _final_embed.md si présent)
  11  hyq_embedding_doc_modular.py             # ré-embedding des questions hyq (auto-sauté si seed_hyq() a déjà copié des embeddings existants)
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # automate_pipeline_example/
_PIPELINE_ROOT = _HERE.parent                     # pipeline_modular/
_PROJECT_ROOT = _PIPELINE_ROOT.parent              # afac-preprocessing/
_SIMPLE  = _PIPELINE_ROOT / "simple_extraction"
_DESCIMG = _PIPELINE_ROOT / "description_image"
_META    = _PIPELINE_ROOT / "metadata"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.paths import load_env  # noqa: E402

_PREPROCESSING_OUTPUT_FILES = _PROJECT_ROOT / "data" / "output_files_preprocessing"
_DEFAULT_OUTPUT_ROOT = _PROJECT_ROOT / "data" / "output_files_v3"


def seed_hyq(doc_name: str, output_root: Path) -> bool:
    """Copie resume.md + intent.json + hyq.json + hyq_<doc>/question_N.csv depuis
    data/output_files_preprocessing/ (v2) vers <output_root>/<doc>/metadata/, sans recalcul.

    resume.md/intent.json/hyq.json sont nécessaires à l'étape 09 (--skip-enhancement lit
    ces fichiers au lieu de les régénérer via VLM — sans ça, metadata_generation_modular.py
    appelle run_enhancement() en interne et écrase hyq.json avec de NOUVELLES questions
    générées depuis le _final.md v3, cassant la comparabilité avec la baseline et le
    pipeline v2 : ce n'est plus la même question posée des deux côtés).
    hyq_<doc>/question_N.csv (embeddings déjà calculés) est nécessaire à l'étape 10 et à
    toute évaluation ultérieure via retrieval_protocol_evaluation.

    Retourne True si la source existe et a été copiée (ou existe déjà côté destination)."""
    src_meta = _PREPROCESSING_OUTPUT_FILES / doc_name / "metadata"
    src_resume = src_meta / "resume.md"
    src_intent = src_meta / "intent.json"
    src_hyq_json = src_meta / "hyq.json"
    src_hyq_dir = src_meta / f"hyq_{doc_name}"

    dst_meta = output_root / doc_name / "metadata"
    dst_resume = dst_meta / "resume.md"
    dst_intent = dst_meta / "intent.json"
    dst_hyq_json = dst_meta / "hyq.json"
    dst_hyq_dir = dst_meta / f"hyq_{doc_name}"

    if dst_resume.exists() and dst_intent.exists() and dst_hyq_json.exists() and dst_hyq_dir.exists():
        print(f"  seed_hyq: déjà présent → {dst_meta}")
        return True

    missing_src = [p.name for p in (src_resume, src_intent, src_hyq_json, src_hyq_dir) if not p.exists()]
    if missing_src:
        print(f"  seed_hyq: source(s) introuvable(s) dans {src_meta} : {missing_src} — étapes 09/10 échoueront.")
        return False

    dst_meta.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_resume, dst_resume)
    shutil.copy2(src_intent, dst_intent)
    shutil.copy2(src_hyq_json, dst_hyq_json)
    shutil.copytree(src_hyq_dir, dst_hyq_dir, dirs_exist_ok=True)
    print(f"  seed_hyq: {src_meta} → {dst_meta} (resume.md + intent.json + hyq.json + {dst_hyq_dir.name}/)")
    return True


def build_steps(
    doc_name: str,
    output_root: Path,
    no_ocr: bool = False,
    image_description: bool = True,
) -> list[tuple[Path, list[str]]]:
    """Liste (script, arguments_supplémentaires). Chaque chemin d'entrée/sortie de chaque
    étape est explicitement routé sous output_root pour ne jamais toucher data/output_files_preprocessing/."""
    doc_out = output_root / doc_name
    step01_args = ["--output-dir", str(doc_out)] + (["--no-ocr"] if no_ocr else [])
    step03_image_flag = "--image-description" if image_description else "--no-image-description"

    doctags_01 = doc_out / f"{doc_name}.doctags"
    doctags_02 = doc_out / f"{doc_name}_reordered.doctags"
    doctags_03 = doc_out / f"{doc_name}_reordered_pictures.doctags"
    hyperlinks_jsonl = doc_out / f"hyperlinks_data_{doc_name}.jsonl"
    doctags_05 = doc_out / f"{doc_name}_url_vlm.doctags"
    md_06 = doc_out / f"{doc_name}_url_vlm.md"
    md_07 = doc_out / f"{doc_name}_vlm_check.md"
    md_descriptions = doc_out / f"{doc_name}_image_descriptions.md"
    md_final = doc_out / f"{doc_name}_final.md"
    md_final_embed = doc_out / f"{doc_name}_final_embed.md"

    return [
        (_SIMPLE  / "pipeline_multietape_modular.py", step01_args),                                        # 01
        (_SIMPLE  / "reordered_doctags_modular.py",
            ["--input", str(doctags_01)]),                                                                 # 02
        (_DESCIMG / "description_image_context_modular.py",
            ["--doctags", str(doctags_02), step03_image_flag]),                                             # 03
        (_SIMPLE  / "url_extaction_modular.py",
            ["--output", str(hyperlinks_jsonl)]),                                                          # 04
        (_SIMPLE  / "url_tuning_vlm_modular.py",
            ["--doctags", str(doctags_03), "--jsonl", str(hyperlinks_jsonl),
             "--output", str(doctags_05), "--prompt-variant", "v3"]),                                       # 05
        (_SIMPLE  / "docling_markdown_converter_modular.py",
            ["--input", str(doctags_05)]),                                                                 # 06
        (_SIMPLE  / "markdown_control_vlm_modular.py",
            ["--markdown", str(md_06), "--output", str(md_07), "--prompt-variant", "v3"]),                  # 07
        (_SIMPLE  / "inject_image_descriptions_modular.py",
            ["--markdown", str(md_07), "--descriptions", str(md_descriptions), "--output", str(md_final)]),  # 08
        (_SIMPLE  / "markdown_tables_to_jsonl_modular.py",
            ["--markdown", str(md_final), "--doc-name", doc_name, "--embed-output", str(md_final_embed)]),   # 09
        (_META    / "metadata_generation_modular.py",
            ["--stage1", str(output_root), "--stage2", str(output_root),
             "--stage3", str(output_root), "--stage4", str(output_root), "--stage5", str(output_root),
             "--skip-enhancement"]),                                                                         # 10
        (_META    / "hyq_embedding_doc_modular.py",
            ["--stage5", str(output_root)]),                                                                # 11
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Full modular pipeline v3 (11 steps) — tables markdown natives, descriptions d'images activées.",
        epilog=(
            "Exemples :\n"
            "  uv run python fullpipeline_modular_v3.py --dotenv .env.test\n"
            "  uv run python fullpipeline_modular_v3.py --input data/input_files/afac/Adhésion/MonDoc.pdf\n"
            "  uv run python fullpipeline_modular_v3.py --dotenv .env.test --from-step 6\n"
            "  uv run python fullpipeline_modular_v3.py --dotenv .env.test --skip-steps 3\n"
            "  uv run python fullpipeline_modular_v3.py --dotenv .env.test --output-root data/output_files_v3_test\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env.test"),
        help="Fichier .env passé à chaque étape (défaut : .env.test).",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        metavar="PDF",
        help=(
            "Chemin vers le PDF d'entrée. Remplace DOC_NAME/DOC_PATH du fichier .env. "
            "DOC_NAME devient le stem du fichier, DOC_PATH le chemin relatif à data/input_files/."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_DEFAULT_OUTPUT_ROOT,
        help=(
            "Racine de sortie pour les 10 étapes — jamais data/output_files_preprocessing/ (v2) ni "
            f"data/baseline_evaluation/ (docling brut). Défaut : {_DEFAULT_OUTPUT_ROOT}."
        ),
    )
    parser.add_argument(
        "--no-seed-hyq",
        action="store_true",
        help="Ne pas copier hyq.json/hyq_<doc>/ depuis data/output_files_preprocessing/ avant l'étape 10.",
    )
    parser.add_argument("--from-step", type=int, default=1, metavar="N", help="Première étape (1-11, défaut : 1).")
    parser.add_argument("--to-step", type=int, default=11, metavar="N", help="Dernière étape (1-11, défaut : 11).")
    parser.add_argument(
        "--skip-steps",
        type=str,
        default="",
        metavar="N[,N...]",
        help="Numéros d'étapes à sauter, ex. --skip-steps 3,7.",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help=(
            "Transmis uniquement à l'étape 01 (pipeline_multietape_modular.py). Mesuré sur ce "
            "corpus (PDF nativement numériques) : texte extrait identique, 3-4x plus rapide "
            "que le passage EasyOCR forcé par défaut."
        ),
    )
    parser.add_argument(
        "--no-image-description",
        action="store_true",
        help=(
            "Désactive les descriptions VLM des images à l'étape 03 (activées par défaut dans "
            "v3, contrairement à v2). Les balises <picture> sont retirées du doctags sans appel "
            "VLM ni texte de description injecté dans _final.md."
        ),
    )
    return parser.parse_args()


def _run_step(step: int, n_total: int, script: Path, dotenv: Path, extra_args: list[str]) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Step {step:02d}/{n_total:02d} — {script.name}")
    if extra_args:
        print(f"  extra args: {extra_args}")
    print(f"{'=' * 60}")
    result = subprocess.run(
        [sys.executable, str(script), "--dotenv", str(dotenv), *extra_args],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n[FAILED] {script.name} exited with code {result.returncode}")
        sys.exit(result.returncode)


def _resolve_doc_name(args: argparse.Namespace, dotenv: Path) -> str:
    """Résout DOC_NAME dans le processus parent (nécessaire pour construire les chemins
    explicites de build_steps). Réplique la logique de fullpipeline_modular_v2.py pour
    --input, sinon charge le .env pour lire DOC_NAME."""
    if args.input:
        input_path = args.input.resolve()
        if not input_path.exists():
            raise SystemExit(f"[ERROR] Input PDF not found: {input_path}")
        os.environ["DOC_NAME"] = input_path.stem.strip()
        input_files_root = (_PROJECT_ROOT / "data" / "input_files").resolve()
        try:
            os.environ["DOC_PATH"] = str(input_path.relative_to(input_files_root))
        except ValueError:
            os.environ["DOC_PATH"] = str(input_path)
        return os.environ["DOC_NAME"]

    load_env(dotenv)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "[ERROR] DOC_NAME introuvable — fournir --input <pdf> ou définir DOC_NAME dans le fichier --dotenv."
        )
    return doc_name


def main() -> None:
    args = parse_args()

    dotenv = args.dotenv.resolve()
    if not dotenv.exists():
        raise SystemExit(f"[ERROR] .env file not found: {dotenv}")

    output_root = args.output_root.resolve()
    if output_root == _PREPROCESSING_OUTPUT_FILES.resolve():
        raise SystemExit(
            f"[ERROR] --output-root ne peut pas pointer sur {_PREPROCESSING_OUTPUT_FILES} (pipeline v2) — "
            "ça écraserait les résultats v2."
        )

    doc_name = _resolve_doc_name(args, dotenv)
    steps = build_steps(
        doc_name, output_root,
        no_ocr=args.no_ocr,
        image_description=not args.no_image_description,
    )
    n_total = len(steps)

    skip: set[int] = {int(s) for s in args.skip_steps.split(",") if s.strip()}
    from_step = max(1, args.from_step)
    to_step = min(n_total, args.to_step)

    selected = [
        (i, script, extra_args)
        for i, (script, extra_args) in enumerate(steps, start=1)
        if from_step <= i <= to_step and i not in skip
    ]

    if not selected:
        raise SystemExit("[ERROR] No steps selected — check --from-step, --to-step, --skip-steps.")

    skip_display = f"  skip: {sorted(skip)}" if skip else ""
    print(
        f"Pipeline v3 starting — doc: {doc_name!r} — steps {from_step}→{to_step}{skip_display} "
        f"— dotenv: {dotenv} — output-root: {output_root}"
    )

    if {10, 11} & {i for i, _, _ in selected} and not args.no_seed_hyq:
        print(f"\nSeeding resume/intent/hyq depuis {_PREPROCESSING_OUTPUT_FILES / doc_name / 'metadata'} ...")
        if not seed_hyq(doc_name, output_root):
            raise SystemExit(
                "[ERROR] seed_hyq a échoué — arrêt avant de lancer les étapes (évite de tourner "
                "1-9 pour rien avant un échec à l'étape 10/11). Lancer le pipeline v2 pour ce "
                "document au préalable, ou passer --no-seed-hyq pour régénérer resume/intent/hyq "
                "via VLM (attention : change les questions HyQ, casse la comparabilité)."
            )
        # seed_hyq() a copié des embeddings HyQ déjà calculés (byte-identiques) — l'étape 11
        # ne ferait que les recalculer en pure perte (appels API embedding gaspillés). --no-seed-hyq
        # court-circuite cet auto-skip : sans seeding, il n'y a rien à réutiliser, l'étape 11 reste utile.
        if 11 in {i for i, _, _ in selected}:
            print("  → étape 11 sautée : embeddings HyQ déjà seedés, ré-embedder serait redondant.")
            selected = [(i, script, extra_args) for i, script, extra_args in selected if i != 11]
            if not selected:
                print(f"\n{'=' * 60}")
                print("  Rien à exécuter (seule l'étape 11 était sélectionnée, auto-sautée ci-dessus).")
                print(f"{'=' * 60}")
                return

    for i, script, extra_args in selected:
        _run_step(i, n_total, script, dotenv, extra_args)

    print(f"\n{'=' * 60}")
    print("  All steps completed successfully.")
    print(f"  Output root: {output_root}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
