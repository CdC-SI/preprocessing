"""Compare un dossier de sortie du pipeline à la référence gelée au lot 0.

Usage: python tools/compare_outputs.py tests/reference data/output_files_preprocessing
Sortie: rapport par document et par fichier — STRICT / STRUCTUREL / TOLERANT / MANQUANT.

Modes de comparaison (§ 3.2 du plan) :
- STRICT      : égalité après normalisation (timestamps ISO, chemins absolus, GEN_ID,
                fins de ligne, ordre des clés JSON) — artefacts déterministes.
- STRUCTUREL  : présence, non-vide, nombre de sections / schéma JSON — sorties VLM,
                dont le texte change à chaque run (pas de cache, contrainte C1).
- TOLERANT    : CSV cellule à cellule, floats via math.isclose(rel_tol=1e-6),
                cellules VLM non comparées textuellement.
- MANQUANT    : fichier de la référence absent du candidat (échec).

Code de sortie non nul si une comparaison STRICT/TOLERANT-déterministe échoue ou si
un fichier manque. Les échecs STRUCTUREL sont des avertissements.

Ne dépend que de la stdlib.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REL_TOL = 1e-6

# Artefacts déterministes : comparaison stricte après normalisation.
STRICT_SUFFIXES = (".doctags", ".md", ".jsonl", ".txt", ".json", ".html")

# Marqueurs de sorties VLM (le texte diffère à chaque run) → structurel.
# Relevé sur le disque, au-delà du § 3.2 : les artefacts de metadata/ sont
# eux aussi générés par VLM/embedding (resume, intent, hyq, embedding),
# et *_final.md contient les descriptions d'images injectées.
VLM_MARKERS = ("_vlm_check", "_image_descriptions", "_url_vlm", "_final.md")
VLM_METADATA_NAMES = ("resume.md", "intent.json", "hyq.json", "embedding.json")

FLOAT_SUFFIXES = (".csv",)

ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?")
ABS_PATH_RE = re.compile(r"/(?:home|tmp|Users)/[^\s\"',)\]]*")
GEN_ID_RE = re.compile(r"(?i)gen[_-]?id[\"']?\s*[:=]\s*[\"']?[\w-]+")


@dataclass
class Verdict:
    mode: str          # STRICT | STRUCTUREL | TOLERANT | MANQUANT | BINAIRE
    ok: bool
    detail: str = ""


def normalize(text: str) -> str:
    """Retire timestamps ISO, chemins absolus, GEN_ID, CRLF ; trie les clés JSON."""
    text = text.replace("\r\n", "\n")
    text = ISO_TS_RE.sub("<TS>", text)
    text = ABS_PATH_RE.sub("<PATH>", text)
    text = GEN_ID_RE.sub("<GEN_ID>", text)
    return text


def _load_json_normalized(path: Path) -> object | None:
    try:
        return json.loads(normalize(path.read_text(encoding="utf-8", errors="replace")))
    except (json.JSONDecodeError, OSError):
        return None


def compare_strict(a: Path, b: Path) -> Verdict:
    """Égalité après normalize() ; les JSON sont comparés structurellement triés."""
    if a.suffix == ".json":
        ja, jb = _load_json_normalized(a), _load_json_normalized(b)
        if ja is not None and jb is not None:
            ok = ja == jb
            return Verdict("STRICT", ok, "" if ok else "contenu JSON différent")
    ta = normalize(a.read_text(encoding="utf-8", errors="replace"))
    tb = normalize(b.read_text(encoding="utf-8", errors="replace"))
    if ta == tb:
        return Verdict("STRICT", True)
    la, lb = ta.splitlines(), tb.splitlines()
    diff_at = next(
        (i for i, (x, y) in enumerate(zip(la, lb), 1) if x != y),
        min(len(la), len(lb)) + 1,
    )
    return Verdict("STRICT", False, f"{len(la)} vs {len(lb)} lignes, 1re diff l.{diff_at}")


def _json_schema(obj: object) -> object:
    """Squelette de types d'un JSON — les valeurs feuilles sont remplacées par leur type."""
    if isinstance(obj, dict):
        return {k: _json_schema(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_json_schema(obj[0])] if obj else []
    return type(obj).__name__


def compare_structural(a: Path, b: Path) -> Verdict:
    """Non-vide, nombre de sections, schéma JSON — jamais le texte exact."""
    if b.stat().st_size == 0 and a.stat().st_size > 0:
        return Verdict("STRUCTUREL", False, "candidat vide")
    if a.suffix == ".json":
        ja, jb = _load_json_normalized(a), _load_json_normalized(b)
        if ja is None or jb is None:
            return Verdict("STRUCTUREL", jb is not None, "JSON illisible")
        ok = _json_schema(ja) == _json_schema(jb)
        return Verdict("STRUCTUREL", ok, "" if ok else "schéma JSON différent")
    ta = a.read_text(encoding="utf-8", errors="replace")
    tb = b.read_text(encoding="utf-8", errors="replace")
    heads_a = sum(1 for line in ta.splitlines() if line.startswith("#"))
    heads_b = sum(1 for line in tb.splitlines() if line.startswith("#"))
    ok = heads_a == heads_b
    return Verdict("STRUCTUREL", ok, "" if ok else f"sections: {heads_a} vs {heads_b}")


def _cells(path: Path) -> list[list[str]]:
    csv.field_size_limit(sys.maxsize)  # colonnes EMBEDDING de plusieurs Ko
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return list(csv.reader(fh))


def compare_floats(a: Path, b: Path) -> Verdict:
    """CSV cellule à cellule : floats via math.isclose, sinon égalité normalisée."""
    ra, rb = _cells(a), _cells(b)
    if len(ra) != len(rb):
        return Verdict("TOLERANT", False, f"{len(ra)} vs {len(rb)} lignes")
    for i, (row_a, row_b) in enumerate(zip(ra, rb), 1):
        if len(row_a) != len(row_b):
            return Verdict("TOLERANT", False, f"l.{i}: {len(row_a)} vs {len(row_b)} colonnes")
        for j, (ca, cb) in enumerate(zip(row_a, row_b), 1):
            try:
                if not math.isclose(float(ca), float(cb), rel_tol=REL_TOL):
                    return Verdict("TOLERANT", False, f"l.{i} c.{j}: {ca!r} != {cb!r}")
                continue
            except ValueError:
                pass
            if normalize(ca) != normalize(cb):
                return Verdict("TOLERANT", False, f"l.{i} c.{j}: cellules différentes")
    return Verdict("TOLERANT", True)


def compare_binary(a: Path, b: Path) -> Verdict:
    ok = a.read_bytes() == b.read_bytes()
    return Verdict("BINAIRE", ok, "" if ok else "octets différents")


def is_vlm_artifact(rel: Path) -> bool:
    name = rel.name
    if any(marker in name for marker in VLM_MARKERS):
        return True
    if rel.parent.name == "metadata" and name in VLM_METADATA_NAMES:
        return True
    # metadata/hyq_<doc>/question_N.csv : questions générées par VLM
    return rel.parent.name.startswith("hyq_")


def is_vlm_csv(rel: Path) -> bool:
    """CSV dont le contenu dérive du VLM/embedding : mismatch = avertissement."""
    return (
        rel.parent.name == "metadata"
        or rel.parent.name.startswith("hyq_")
        or any(marker in rel.name for marker in VLM_MARKERS)
    )


def compare_file(ref: Path, cand: Path, rel: Path) -> tuple[Verdict, bool]:
    """Retourne (verdict, bloquant) — bloquant = un échec fait échouer le run."""
    if ref.suffix in FLOAT_SUFFIXES:
        return compare_floats(ref, cand), not is_vlm_csv(rel)
    if is_vlm_artifact(rel):
        return compare_structural(ref, cand), False
    if ref.suffix in STRICT_SUFFIXES:
        return compare_strict(ref, cand), True
    return compare_binary(ref, cand), False


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    ref_root, cand_root = Path(argv[1]), Path(argv[2])
    if not ref_root.is_dir() or not cand_root.is_dir():
        print(f"Dossier introuvable: {ref_root if not ref_root.is_dir() else cand_root}")
        return 2

    ref_files = {
        p.relative_to(ref_root): p
        for p in sorted(ref_root.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    }
    cand_files = {
        p.relative_to(cand_root): p
        for p in sorted(cand_root.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    }

    failures = warnings = 0
    counts: dict[str, int] = {}
    for rel, ref_path in ref_files.items():
        cand_path = cand_files.pop(rel, None)
        if cand_path is None:
            failures += 1
            counts["MANQUANT"] = counts.get("MANQUANT", 0) + 1
            print(f"MANQUANT    ✗ {rel}")
            continue
        verdict, blocking = compare_file(ref_path, cand_path, rel)
        counts[verdict.mode] = counts.get(verdict.mode, 0) + 1
        if verdict.ok:
            continue
        if blocking:
            failures += 1
            print(f"{verdict.mode:<11} ✗ {rel} — {verdict.detail}")
        else:
            warnings += 1
            print(f"{verdict.mode:<11} ⚠ {rel} — {verdict.detail}")

    for rel in cand_files:
        warnings += 1
        print(f"EXTRA       ⚠ {rel} (absent de la référence)")

    total = len(ref_files)
    summary = ", ".join(f"{mode}: {n}" for mode, n in sorted(counts.items()))
    print(f"\n{total} fichiers comparés ({summary})")
    print(f"Échecs bloquants: {failures} — Avertissements: {warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
