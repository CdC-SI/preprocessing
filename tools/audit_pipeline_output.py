"""
audit_pipeline_output.py — Post-run health check for a pipeline output tree.

Scans a --stage5 root (data/output_files_preprocessing) and flags documents
where a step likely failed silently — several scripts in this pipeline fall back to
passthrough/original content on VLM error rather than raising, so a batch run can finish
with exit code 0 while individual documents are missing corrections. This script does not
modify anything; it is read-only and safe to run repeatedly.

Checks per document:
  - stage 1 presence   : <doc>.doctags, <doc>.md
  - final content       : <doc>_final.md exists and is non-empty
  - leftover placeholders: [[[IMAGE_DESC:N]]] markers not replaced by
                            inject_image_descriptions.py (VLM description or
                            injection failed for that image)
  - image coverage      : count of <picture> tags in the raw doctags vs "## OK" sections in
                            <doc>_image_descriptions.md (only meaningful if image description
                            was enabled for this run — reported as info, not a failure, when
                            the descriptions file is empty/absent, since that's the expected
                            state with ENABLE_IMAGE_DESCRIPTION=false)
  - metadata presence   : metadata/resume.md, intent.json, hyq.json, embedding.json
  - hyq embedding count : len(hyq.json) vs number of metadata/hyq_<doc>/question_*.csv files
  - table presence      : number of tables/*.csv vs whether any table-shaped content
                            (markdown pipe row or JSON object line) appears in the final
                            content — informational, heuristic

Usage:
    uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing
    uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing --json
"""
import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\[\[\[IMAGE(?:\\)?_DESC:\d+\]\]\]")
MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
JSONL_ROW_RE = re.compile(r'^\s*\{".*"\s*:.*\}\s*$', re.MULTILINE)


def discover_docs(stage5_dir: Path) -> list[str]:
    """Any directory <name>/ containing a canonical <name>.doctags (the raw stage-1
    export) is treated as a processed doc. Matching on the exact <dirname>.doctags
    filename (not a glob over every *.doctags variant) avoids counting each doc multiple
    times for its _reordered/_url_vlm/etc. intermediate doctags files.

    ⚠ Lot F1 : la recherche est RÉCURSIVE et renvoie des chemins RELATIFS à
    stage5_dir (ex. "afac/Adhésion/Mineur"), la sortie reproduisant désormais
    l'arborescence d'entrée. Deux documents homonymes rangés dans des dossiers
    différents apparaissent donc comme deux entrées distinctes.
    """
    return sorted(
        str(p.relative_to(stage5_dir))
        for p in stage5_dir.rglob("*")
        if p.is_dir() and (p / f"{p.name}.doctags").exists()
    )


def _check_stage1(doc_dir: Path, doc_name: str) -> list[str]:
    issues = []
    if not (doc_dir / f"{doc_name}.doctags").exists():
        issues.append("missing raw .doctags (stage 1 never ran or failed)")
    if not (doc_dir / f"{doc_name}.md").exists():
        issues.append("missing raw .md (stage 1 text export)")
    return issues


def _resolve_final_content(doc_dir: Path, doc_name: str) -> Path | None:
    """<doc>_final_embed.md if present, else <doc>_final.md — same preference order used by
    embedding_metadata.py and metadata_generation.py."""
    final_embed = doc_dir / f"{doc_name}_final_embed.md"
    final_md = doc_dir / f"{doc_name}_final.md"
    if final_embed.exists():
        return final_embed
    if final_md.exists():
        return final_md
    return None


def _check_leftover_placeholders(content: str) -> tuple[list[str], int]:
    leftover = PLACEHOLDER_RE.findall(content)
    issues = []
    if leftover:
        issues.append(f"{len(leftover)} unreplaced [[[IMAGE_DESC:N]]] placeholder(s) in final content")
    return issues, len(leftover)


def _check_image_coverage(doc_dir: Path, doc_name: str, raw_doctags: Path) -> tuple[list[str], dict]:
    """description_image_context.py always writes an (empty) markdown file even when
    ENABLE_IMAGE_DESCRIPTION is off, so an empty/absent file means "disabled for this run"
    (informational) — only a non-empty file with fewer OK sections than pictures found means
    the VLM step actually ran and failed on some images (real issue)."""
    n_pictures_raw = len(re.findall(r"<picture>", raw_doctags.read_text(encoding="utf-8"))) if raw_doctags.exists() else 0
    desc_path = doc_dir / f"{doc_name}_image_descriptions.md"
    n_described = 0
    issues = []
    description_enabled = desc_path.exists() and desc_path.stat().st_size > 0
    if description_enabled:
        desc_text = desc_path.read_text(encoding="utf-8")
        n_described = len(re.findall(r"^## OK - Image \d+/\d+", desc_text, re.MULTILINE))
        if n_pictures_raw > 0 and n_described < n_pictures_raw:
            issues.append(f"image descriptions incomplete: {n_described}/{n_pictures_raw} described")
    info = {
        "pictures_detected": n_pictures_raw,
        "pictures_described": n_described,
        "image_description_enabled": description_enabled,
    }
    return issues, info


def _check_metadata_presence(meta_dir: Path) -> list[str]:
    issues = []
    for fname in ("resume.md", "intent.json", "hyq.json", "embedding.json"):
        fpath = meta_dir / fname
        if not fpath.exists():
            issues.append(f"missing metadata/{fname}")
        elif fpath.stat().st_size == 0:
            issues.append(f"metadata/{fname} is empty")
    return issues


def _check_hyq_embeddings(meta_dir: Path, doc_name: str) -> tuple[list[str], dict]:
    hyq_path = meta_dir / "hyq.json"
    if not hyq_path.exists():
        return [], {}

    issues = []
    try:
        n_questions = len(json.loads(hyq_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return ["hyq.json is not valid JSON"], {}

    hyq_dir = meta_dir / f"hyq_{doc_name}"
    n_embedded = len(list(hyq_dir.glob("question_*.csv"))) if hyq_dir.exists() else 0
    if n_embedded != n_questions:
        issues.append(f"hyq question/embedding mismatch: {n_questions} question(s), {n_embedded} embedded")
    return issues, {"hyq_questions": n_questions, "hyq_embedded": n_embedded}


def _check_table_visibility(doc_dir: Path, content: str) -> tuple[list[str], dict]:
    """Heuristic, informational: does any table-shaped content (markdown pipe row or JSON
    object line) appear in the final content for each table Docling extracted."""
    tables_dir = doc_dir / "tables"
    n_tables = len(list(tables_dir.glob("*.csv"))) if tables_dir.exists() else 0
    if n_tables == 0:
        return [], {}

    visible = bool(MD_TABLE_ROW_RE.search(content)) or bool(JSONL_ROW_RE.search(content))
    issues = []
    if not visible:
        issues.append(f"{n_tables} table(s) extracted but no table-shaped content found in final markdown")
    return issues, {"tables_extracted": n_tables, "tables_visible_in_content": visible}


def audit_doc(stage5_dir: Path, doc_ref: str) -> dict:
    """*doc_ref* est le chemin relatif du dossier document sous stage5_dir
    (lot F1) — le nom du document en est le dernier segment."""
    doc_dir = stage5_dir / doc_ref
    doc_name = Path(doc_ref).name
    meta_dir = doc_dir / "metadata"
    info: dict = {"doc": doc_ref}
    issues: list[str] = []

    issues += _check_stage1(doc_dir, doc_name)

    content_path = _resolve_final_content(doc_dir, doc_name)
    if content_path is None:
        issues.append("missing _final.md — pipeline did not complete for this doc")
        info["issues"] = issues
        return info

    content = content_path.read_text(encoding="utf-8")
    info["content_source"] = content_path.name
    info["content_chars"] = len(content)
    if not content.strip():
        issues.append("_final.md is empty")

    placeholder_issues, n_leftover = _check_leftover_placeholders(content)
    issues += placeholder_issues
    info["leftover_placeholders"] = n_leftover

    image_issues, image_info = _check_image_coverage(doc_dir, doc_name, doc_dir / f"{doc_name}.doctags")
    issues += image_issues
    info.update(image_info)

    issues += _check_metadata_presence(meta_dir)

    hyq_issues, hyq_info = _check_hyq_embeddings(meta_dir, doc_name)
    issues += hyq_issues
    info.update(hyq_info)

    table_issues, table_info = _check_table_visibility(doc_dir, content)
    issues += table_issues
    info.update(table_info)

    info["issues"] = issues
    return info


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only health check for a pipeline output tree (data/output_files_preprocessing).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing\n"
            "  uv run python tools/audit_pipeline_output.py --stage5 data/output_files_preprocessing --json\n"
        ),
    )
    parser.add_argument("--stage5", type=Path, required=True, help="Racine de sortie du pipeline à auditer.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute au lieu du résumé lisible.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage5_dir = args.stage5.resolve()
    if not stage5_dir.exists():
        raise SystemExit(f"Erreur : dossier introuvable — {stage5_dir}")

    docs = discover_docs(stage5_dir)
    if not docs:
        raise SystemExit(f"Aucun document trouvé dans {stage5_dir}")

    results = [audit_doc(stage5_dir, doc) for doc in docs]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(1 if any(r["issues"] for r in results) else 0)

    n_clean = sum(1 for r in results if not r["issues"])
    print(f"Audit : {stage5_dir}")
    print(f"{len(docs)} document(s) — {n_clean} sans problème détecté, {len(docs) - n_clean} avec au moins un signal\n")

    for r in results:
        if not r["issues"]:
            continue
        print(f"[{r['doc']}]")
        for issue in r["issues"]:
            print(f"  - {issue}")
        print()

    sys.exit(1 if any(r["issues"] for r in results) else 0)


if __name__ == "__main__":
    main()
