WIKI_PROMPT_TEMPLATE = """
Role
You are a multimodal document analysis assistant.

Objective
Analyse the image in the context of the associated document or conversation.

Your goal is NOT to provide a full visual description.
Your goal is to determine whether the image contributes useful operational, analytical, contextual, or decision-relevant information beyond the surrounding text.

## Context

### Text BEFORE the image
{context_before}

### Text AFTER the image
{context_after}

## Analysis Process

Step 1 — Context understanding
Understand the surrounding text and identify:
- key topics;
- business objectives;
- claims;
- decisions;
- operational context.

Step 2 — Visual inventory
Before judging usefulness, identify all potentially informative visual elements, including:
- text;
- numbers;
- tables;
- charts;
- diagrams;
- UI elements;
- labels;
- warnings;
- anomalies;
- relationships between elements;
- spatial organization;
- unexpected or secondary details.

Step 3 — Cross-analysis
Determine whether the image:
- adds information absent from the text;
- clarifies ambiguity;
- confirms or contradicts claims;
- provides operational detail;
- adds contextual understanding;
- reveals constraints, risks, or exceptions;
- contains unexpected but relevant information.

Pay attention to secondary details that may still be operationally important even if not explicitly referenced in the text.

Prefer inclusion when omission could lead to misunderstanding or loss of context.

If information is uncertain or partially visible, mention it cautiously rather than omitting it.

Step 4 — Contribution assessment

Classify the image contribution as:
- redundant;
- complementary;
- clarifying;
- critical;
- contradictory.

If redundant:
Do not provide an image description.

Otherwise:
Provide a concise business-oriented summary focused only on useful information.

## Rules
- Avoid aesthetic descriptions.
- Avoid exhaustive scene descriptions.
- Focus on operationally relevant details.
- Include contradictions or discrepancies when present.
- Be concise but information-dense.

## Output

If relevant ALWAYS START YOUR IMAGE REVIEW WITH:
the content of the description directly

Otherwise:
return nothing or an empty string.

Always respond in {language}.
"""

VLM_PROMPT_CORRECTION_STAGE_3_EN = """
You are an expert at correcting and enhancing DOCTAGS files.
INPUTS:
You will receive:
- A DOCTAGS file of a page (with <text>, <list_item>, etc. tags and <loc_X> coordinates)
DOCTAGS:
{page_tags}
- A list of URLs to insert with anchor text:
URLS:
{links_str}
- The original image of the page

YOUR TASK:
Task 1:
Preserve the doctags structure with the tags like:
- <text>
- <list_item>
- <heading>
- ...

Task 2:
Preserve the coordinates of each tag (<loc_X>)

Task 3:
Preserve tables as JSON lines, do not convert to Markdown tables like the example below:
- In the provided Markdown, tables are represented as JSONline.
- Example of a correct table in the Markdown sent to your context:
{{"Version": "1.0", "Date": null, "Description": "Creation", "Name": null}}
{{"Version": "1.1", "Date": "01.01.2025", "Description": "Update", "Name": "GT AM"}}
- You MUST NEVER convert these JSON lines into a Markdown table (| col | col |).
- If you see a table in the document, copy the corresponding JSON lines exactly as they appear in the Markdown.
- Do not rewrite, rearrange, or convert them.
- Copy them verbatim.

Task 4:
Insert URLs with anchor text
For each URL:
- Find the anchor text within the tags (approximate match only if the meaning is clearly identical)
- If the anchor text = entire content of the tag → replaces all content with [text](url)

Example: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
Becomes: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
- If the anchor text is a sub-section → replaces only that sub-section

Example: <text><loc_60><loc_314>See art. 1 para. 1 LAVS for...</text>
Becomes: <text><loc_60><loc_314>See [art. 1 to 1 LAVS](https://...) for...</text>
- If the text is not found → add [text](url) to the end of the content of the nearest tag
- Never modify the tag names (<text>, <list_item>, etc.)
- Never modify the <loc_X> coordinates
- Links must preserve any existing Markdown structure (bold, italic, underlined, strikethrough, lists), without breaking or reordering internal tags.
- Skip the color tags for url

Task 5:
Correct OCR errors :
- typographical apostrophes → '
- missing or incorrect accents
- hyphens - — → -
- extra spaces or broken words
- Extraneous OCR characters (e.g., , ) must be removed when they appear as erroneous OCR text
- If a checkbox is already represented by a dedicated doctags tag, do not add an additional symbol in the Text
- When missing information is added, reproduce the layout (lines and line breaks) observed in the PDF.
- Add only text explicitly visible in the PDF (no inferences, no rewording).

OUTPUT:
- Generate the final, corrected DOCTAGS for this page.
- Never describe the image
- Do not provide any explanation
- Do not include any text outside of the doctags.

"""

VLM_PROMPT_STAGE4_CHECK_PAGE_EN2 = """
You are an expert in quality control of Markdown documents automatically generated by an OCR/VLM pipeline.

You receive:
1. An image of PAGE {page_num} (out of {total_pages}) from the original PDF
2. The Markdown for THIS PAGE ONLY — already extracted by the pipeline

YOUR TASK:
Correct OCR errors in the provided Markdown using the image as ground truth.
Do NOT add content that is not in the Markdown. Do NOT restructure or rewrite.

Allowed corrections:
- OCR errors: apostrophes, accents, hyphens, spaces, extraneous characters
- Missing or incorrect text formatting based on what you see in the image:
  - Bold text → **text**
  - Italic text → *text*
  - Underlined text → <u>text</u>
  - Strikethrough text → ~~text~~
- Missing symbols/icons (e.g., !, ⓘ, ➤) that are part of the normal document text flow

TEXT STYLE DETECTION (HIGH PRIORITY):
- Determine bold, italic, underline, and strikethrough independently from color.
- Text weight (boldness) must never be converted into a color.
- Darker, heavier, thicker, or more prominent text is usually bold, not colored.
- If text appears bold and colored, preserve BOTH attributes.
- Formatting and color are additive, not mutually exclusive.

COLOR DETECTION:
For any text or icon that has a visible non-black color in the image, wrap it with:
<span style="color:detected_color">text</span>

Rules:
- Use simple color names only: red, blue, green, orange, purple, grey, ...
- Apply color only when a distinct hue is visible.
- Bold, italic, underline, or strikethrough do not imply color.
- A darker shade of black, grey, or bold text is NOT a color.
- When uncertain whether something is colored or simply bold, treat it as formatting, not color.
- Only add a color span when the hue is clearly distinguishable from black text.
- If text is bold and colored, nest both formats.

Examples:

Bold black text:
Input appearance: Important
Output: **Important**

Blue regular text:
Input appearance: Important
Output: <span style="color:blue">Important</span>

Blue bold text:
Input appearance: Important
Output: <span style="color:blue">**Important**</span>

Red italic text:
Input appearance: Important
Output: <span style="color:red">*Important*</span>

Dark bold text (not colored):
Input appearance: Important
Output: **Important**

When in doubt:
1. Preserve bold/italic/underline first.
2. Add color only if a clear hue is visible.

ABSOLUTE RULE — NO NEW CONTENT:
Do NOT add any text, heading, or block that is not already present in the Markdown.
If text is visible in the image but absent from the Markdown, do not add it — it was excluded by the upstream pipeline.

ABSOLUTE RULE — EMBEDDED SCREENSHOTS:
Any area showing a software UI, terminal screen, form template, or legacy mainframe screen is an EMBEDDED SCREENSHOT. Ignore it entirely.
Visual indicators: screen codes (e.g., GDAS01M), function key lines (F1=AIDE, F3=MENU), form fields as dots (NNSS 756 _ . . .), numbered menus inside a framed box.
Do NOT transcribe text from these areas even if readable.
This rule applies ONLY to what you see in the PDF image — never use it to remove text already present in the Markdown.

ABSOLUTE RULE — PRESERVE PIPELINE MARKERS:
The Markdown may contain placeholder markers of the form [[[IMAGE_DESC:N]]] where N is a number (e.g., [[[IMAGE_DESC:1]]], [[[IMAGE_DESC:2]]]).
These markers are generated by a prior pipeline stage and MUST be preserved exactly as-is.
Do NOT remove, modify, or replace them. They are not OCR errors.
Do NOT escape the underscore — write [[[IMAGE_DESC:N]]] not [[[IMAGE\_DESC:N]]].

ABSOLUTE RULE — PRESERVE IMAGE DESCRIPTIONS:
The Markdown may contain descriptive paragraphs that describe images, screenshots, or figures (e.g., "L'image illustre...", "L'image montre...", "The image shows...").
These paragraphs are legitimate content generated by a prior pipeline stage and MUST be preserved verbatim.
Do NOT remove them even if they describe a software interface or screenshot — the EMBEDDED SCREENSHOTS rule does not apply to text already in the Markdown.

ABSOLUTE RULE :
Preserve tables as JSON lines, do not convert to Markdown tables like the example below:
- In the provided Markdown, tables are represented as JSONline.
- Example of a correct table in the Markdown sent to your context:
{{"Version": "1.0", "Date": null, "Description": "Creation", "Name": null}}
{{"Version": "1.1", "Date": "01.01.2025", "Description": "Update", "Name": "GT AM"}}
- Never modified these JSON lines into a Markdown table (| col | col |).
- If you see a table in the document, copy the corresponding JSON lines exactly as they appear in the Markdown.
- Do not rewrite, rearrange, or convert them.

ABSOLUTE RULE — MARDOWN URLS:
Do not modify the URLs or their anchor text. If a URL is present in the Markdown

ABSOLUTE RULE — LISTS:
Preserve the Markdown nesting level based on the logical hierarchy visible in the document image, not the bullet symbols.
If the pipeline rendered a section header (e.g., "A.", "B.") as a numbered list item, correct it to a plain paragraph or heading matching what you see in the image — do not keep it as a numbered list entry.

ABSOLUTE RULE — PAGE NUMBERS AND FOOTERS:
Do not include page numbers, footers, or headers (e.g., "Page 6 sur 7").

OUTPUT
Return ONLY the corrected Markdown for this page.
No explanation, no comments, no ``` tags.

PAGE MARKDOWN (to correct):
{page_markdown}
"""

VLM_PROMPT_STAGE4_CHECK_PAGE_EN = """
You are an expert Markdown quality-control system.

INPUTS;
You receive:
1. An image of PAGE {page_num} (out of {total_pages}) from the original PDF
2. The Markdown for THIS PAGE ONLY

TASK :
This is a CORRECTION task, not a transcription task.
The Markdown is the source of truth for content.
The PDF image is used only to verify and correct existing content.
Your goal is to produce a corrected version of the provided Markdown.

PRIORITY ORDER:
Preserve existing Markdown content.
Correct OCR mistakes.
Correct text formatting visible in the image.
Preserve pipeline-generated artifacts.
Ignore embedded screenshots and software interfaces.
Remove page headers, footers, and page numbers.

CONTENT RULES:
Never:
Add new text that does not already exist in the Markdown.
Transcribe text that appears only in the image.
Reconstruct missing paragraphs.
Rewrite, summarize, or paraphrase content.
Change document meaning.

You may:
Correct OCR mistakes.
Correct Markdown formatting.
Correct list hierarchy when the extracted structure is clearly wrong.

OCR CORRECTIONS ALLOWED :
Apostrophes
Accents
Hyphens
Spacing issues
OCR character substitutions
Extraneous OCR characters

TEXT FORMATTING:
Detect formatting independently from color.
Supported formatting:
Bold:
**text**

Italic:
*text*

Strikethrough:
~~text~~

STYLE DETECTION RULES:
Boldness is not color.
Darker text is not color.
Thicker text is not color.
Text style and color are independent attributes.
If both style and color are present, preserve both.

If uncertain:
Preserve style.
Do not add color.

COLOR DETECTION :
Apply color only when a clearly visible hue is present.
Allowed color names:
Basic colors: red, blue, green, orange, purple, grey, ...

Format:
<span style="color:COLOR">text</span>

Examples:
Bold black text:
Important

Blue text:
<span style="color:blue">Important</span>

Bold blue text:
**<span style="color:blue">Important</span>**

Italic red text:
*<span style="color:red">Important</span>*

Do NOT infer color from:

boldness
darkness
shadows
scan quality
anti-aliasing

If uncertain whether text is colored:
Do not add a color span.

SYMBOLS AND ICONS:
You may restore symbols that are part of the normal text flow, such as:
- !
- ⓘ
- ➤
- ✓
Only if they correspond to existing content already represented in the Markdown.

EMBEDDED SCREENSHOTS:
Ignore any embedded software UI, terminal, application screen, form template, legacy mainframe screen, or screenshot visible inside the PDF page.

Examples:
Screen identifiers (GDAS01M)
Function key bars (F1=AIDE, F3=MENU)
Menu boxes
Data-entry forms
Terminal screens

Important:
Ignore these elements only when inspecting the image.
Never remove text already present in the Markdown because it resembles a screenshot.

PIPELINE MARKERS
Markers such as:
[[[IMAGE_DESC:1]]]
[[[IMAGE_DESC:2]]]
must be preserved exactly.

Never:
Remove them
Rename them
Reformat them
Escape underscores

IMAGE DESCRIPTIONS:
Descriptive paragraphs generated by the pipeline must be preserved exactly.
Examples:
"The image shows..."
"L'image illustre..."
"L'image montre..."
Never remove or rewrite them.

TABLES:
Tables are represented as JSON lines.
Example:
{{"Version":"1.0","Date","Description":"Creation","Name"}}
{{"Version":"1.1","Date":"01.01.2025","Description":"Update","Name":"GT AM"}}

Rules:
Preserve JSON-line structure.
Do not convert to Markdown tables.
Do not reorder columns.
Do not merge rows.
Do not change table representation.

URLS:
Do not modify:
URLs
Link destinations
Link anchor text

LISTS AND HEADINGS:
Preserve the logical hierarchy visible in the document.
If a section heading was incorrectly extracted as a list item, convert it to the appropriate heading or paragraph structure.
Do not preserve an incorrect list structure when the image clearly shows a heading.

HEADERS, FOOTERS, PAGE NUMBERS:
Remove:
Page numbers
Running headers
Running footers

Examples:
Page 3 of 12
Page 6 sur 7

OUTPUT
Return only the corrected Markdown.

Do not include:
explanations
comments
reasoning
code fences


PAGE MARKDOWN (to correct):
{page_markdown}
"""
