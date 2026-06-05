# filepath: /home/u89606036/Dev_Python_Stage/preprocessing/matthias_guideline_preprocessing/stage1_multi_steps_detection/cleaning_doctags.py
import re
import os
import unicodedata
from pathlib import Path
from dotenv import load_dotenv

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
load_dotenv(dotenv_path=dotenv_path)

try:
    from wordfreq import zipf_frequency
    WORDFREQ_AVAILABLE = True
except ImportError:
    WORDFREQ_AVAILABLE = False

LANG = os.environ.get("DOC_LANG", "fr")

# ── Cleaning functions ───────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\u00A0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def fix_encoding_artifacts(text: str) -> str:
    text = text.replace('\x00', '')
    text = text.replace('\ufffd', '')
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x80-\xFF\u0100-\uFFFC]', '', text)
    text = unicodedata.normalize('NFC', text)
    return text

def deduplicate_lines(text: str, window: int = 3) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        normalized = re.sub(r'[\[\]☐☑□✓✗\s]', '', line).strip().lower()
        recent = [re.sub(r'[\[\]☐☑□✓✗\s]', '', l).strip().lower() for l in out[-window:]]
        if normalized and normalized not in recent:
            out.append(line)
    return '\n'.join(out)

def ensure_end_punctuation(text: str) -> str:
    def fix(line: str) -> str:
        if re.search(r'\w$', line.rstrip()):
            return line.rstrip() + '.'
        return line
    return '\n'.join(fix(l) for l in text.splitlines())

def rejoin_split_words(text: str, lang: str = LANG, min_zipf: float = 3.5) -> str:
    tokens = re.split(r'(\s+)', text)
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if i + 2 < len(tokens):
            next_tok = tokens[i+2]
            candidate = tok + next_tok
            if tok.isalpha() and next_tok.isalpha():
                join = False
                if WORDFREQ_AVAILABLE:
                    freq_joined = zipf_frequency(candidate, lang)
                    freq_tok   = zipf_frequency(tok, lang)
                    freq_next  = zipf_frequency(next_tok, lang)
                    if freq_joined >= min_zipf and freq_joined > max(freq_tok, freq_next) + 1:
                        join = True
                if join:
                    out.append(candidate)
                    i += 3
                    continue
        out.append(tok)
        i += 1
    return ''.join(out)

def fix_missing_linebreaks(text: str) -> str:
    text = re.sub(r'([.!?])\s{2,}([A-ZÉÈÀÙÂÊÎÔÛÄËÏÖÜ])', r'\1\n\2', text)
    text = re.sub(r'\s+(→\s*[A-Z])', r'\n\1', text)
    text = re.sub(r'\s+(\d+\.\s+[A-Z])', r'\n\1', text)
    return text

def clean_content(text: str) -> str:
    t = normalize_text(text)
    t = fix_encoding_artifacts(t)
    t = deduplicate_lines(t)
    t = fix_missing_linebreaks(t)
    t = ensure_end_punctuation(t)
    t = rejoin_split_words(t)
    return t

# ── Tag → Markdown conversion ────────────────────────────────────────────────

def _inner(m: re.Match) -> str:
    """Extract and clean inner content from a tag match."""
    return clean_content(m.group(1).strip())

def doctags_to_markdown(text: str) -> str:

    # ── Remove private-use symbols ──────────────────────────────────────────
    text = text.replace('\uf063', '').replace('\uf14a', '')

    # ── Tags to remove entirely (not useful for LLM) ───────────────────────
    text = re.sub(r'<page_header>(?:<loc_\d+>)*.*?</page_header>', '', text, flags=re.DOTALL)
    text = re.sub(r'<page_footer>(?:<loc_\d+>)*.*?</page_footer>', '', text, flags=re.DOTALL)
    text = re.sub(r'<document_index>(?:<loc_\d+>)*.*?</document_index>', '', text, flags=re.DOTALL)

    # ── Checkboxes ─────────────────────────────────────────────────────────
    text = re.sub(
        r'<checkbox_unselected>(?:<loc_\d+>)*\s*[\uf063☐]?\s*(.*?)</checkbox_unselected>',
        lambda m: f"- [ ] {_inner(m)}",
        text, flags=re.DOTALL
    )
    text = re.sub(
        r'<checkbox_selected>(?:<loc_\d+>)*\s*[\uf14a☑]?\s*(.*?)</checkbox_selected>',
        lambda m: f"- [x] {_inner(m)}",
        text, flags=re.DOTALL
    )

    # ── Title / Section headers ─────────────────────────────────────────────
    # text = re.sub(
    #     r'<title>(?:<loc_\d+>)*\s*(.*?)</title>',
    #     lambda m: f"# {_inner(m)}",
    #     text, flags=re.DOTALL
    # )
    # text = re.sub(
    #     r'<section_header_level_(\d+)>(?:<loc_\d+>)*\s*(.*?)</section_header_level_\1>',
    #     lambda m: f"{'#' * int(m.group(1))} {clean_content(m.group(2).strip())}",
    #     text, flags=re.DOTALL
    # )

    # ── Lists ───────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<list_item>(?:<loc_\d+>)*\s*(.*?)</list_item>',
    #     lambda m: f"- {_inner(m)}",
    #     text, flags=re.DOTALL
    # )

    # ── Code ────────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<code>(?:<loc_\d+>)*\s*(.*?)</code>',
    #     lambda m: f"```\n{_inner(m)}\n```",
    #     text, flags=re.DOTALL
    # )

    # ── Formula ─────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<formula>(?:<loc_\d+>)*\s*(.*?)</formula>',
    #     lambda m: f"`{_inner(m)}`",
    #     text, flags=re.DOTALL
    # )

    # ── Caption ─────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<caption>(?:<loc_\d+>)*\s*(.*?)</caption>',
    #     lambda m: f"*{_inner(m)}*",
    #     text, flags=re.DOTALL
    # )

    # ── Footnote ────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<footnote>(?:<loc_\d+>)*\s*(.*?)</footnote>',
    #     lambda m: f"> ¹ {_inner(m)}",
    #     text, flags=re.DOTALL
    # )

    # ── Key-Value Region ─────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<key_value_region>(?:<loc_\d+>)*\s*(.*?)</key_value_region>',
    #     lambda m: f"**{_inner(m)}**",
    #     text, flags=re.DOTALL
    # )

    # ── Picture ─────────────────────────────────────────────────────────────
    # text = re.sub(
    #     r'<picture>(?:<loc_\d+>)*\s*(.*?)</picture>',
    #     lambda m: f"![image]({_inner(m)})" if _inner(m) else "",
    #     text, flags=re.DOTALL
    # )

    # ── Plain text / form / other ────────────────────────────────────────────
    text = re.sub(r'</?[\w\-]+(?: [^>]*)?>', '', text)  # strip remaining tags
    text = re.sub(r'<loc_\d+>', '', text)                # strip leftover locs
    text = re.sub(r'\n{3,}', '\n\n', text)               # normalize newlines

    return text.strip()

# ── process_doctags ──────────────────────────────────────────────────────────

def process_doctags(path: Path, out_path: Path) -> None:
    raw = path.read_text(encoding='utf-8')
    result = doctags_to_markdown(raw)
    out_path.write_text(result, encoding='utf-8')
    print(f"Processed {path} -> {out_path}")

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DOC_NAME = os.environ.get("DOC_NAME", "")
    project_root = Path(__file__).resolve().parent.parent
    base = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME
    src = base / f"{DOC_NAME}.doctags"
    dst = base / f"{DOC_NAME}_cleaned.doctags"
    bak = src.with_suffix(src.suffix + ".bak")

    if not src.exists():
        print(f"File not found: {src}")
        raise SystemExit(1)

    bak.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    process_doctags(src, dst)