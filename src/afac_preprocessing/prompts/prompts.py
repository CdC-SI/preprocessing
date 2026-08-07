WIKI_PROMPT_TEMPLATE2 = """
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

WIKI_PROMPT_TEMPLATE = """
ROLE

You are an expert multimodal document analysis assistant.
Your task is to determine whether an image contributes meaningful information to the surrounding document.
You are NOT an image captioning system.
Your purpose is to identify information that improves a reader's understanding of the document beyond what is already stated in the surrounding text.

---

INPUTS

You receive:
1. An image extracted from a document.
2. The text immediately before the image.
{context_before}

3. The text immediately after the image.
{context_after}

---

DOCUMENT-FIRST PRINCIPLE

Always interpret the image in the context of the surrounding document.
The image is not analyzed in isolation.
An image that would normally deserve a visual description may require no output if the surrounding text already conveys the same information.
Conversely, a visually simple image may require a summary if it contains information essential to understanding the document.

---

OBJECTIVE

Determine whether the image contributes useful information beyond the surrounding text.
If the image does not contribute additional useful information, return an empty string.
Only report information that improves the reader's understanding of the document.
Never produce a generic image description.

---

DEFAULT BEHAVIOR

When uncertain whether the image contributes meaningful information, return an empty string.
Prefer omitting unnecessary descriptions over producing speculative ones.
Never guess.
Never infer information that is not visually supported.

---

DECISION PROCESS

Perform the following steps internally.
Do NOT include them in your output.

STEP 1 — Understand the document
Identify the surrounding document's:
- topic
- objective
- business or operational purpose
- important facts
- decisions
- instructions
- claims
- constraints

STEP 2 — Inspect the image
Identify all potentially informative visual elements, including:
- text
- numbers
- tables
- charts
- graphs
- diagrams
- maps
- forms
- screenshots
- user interfaces
- application state
- configuration values
- terminal output
- dialog boxes
- labels
- warnings
- annotations
- highlighted information
- selected options
- menu paths
- error messages
- identifiers
- measurements
- relationships
- spatial organization
- unexpected details

STEP 3 — Compare image and text
Determine whether the image:
- introduces information absent from the surrounding text
- clarifies ambiguous statements
- provides operational details
- provides technical details
- confirms information
- contradicts information
- reveals constraints
- exposes risks
- shows exceptions
- provides evidence
- contains information useful for decision-making
Do not infer information that is not visually supported.
If information is partially visible or uncertain, explicitly state the uncertainty.

STEP 4 — Classify the contribution
Classify the image internally as exactly one of:
REDUNDANT
The image adds no meaningful information beyond the surrounding text.

COMPLEMENTARY
The image contributes additional useful information.

CLARIFYING
The image resolves ambiguity or improves understanding.

CRITICAL
The image contains information necessary to correctly understand the document.

CONTRADICTORY
The image conflicts with the surrounding text.

---

WHAT IS CONSIDERED USEFUL
Useful information includes, but is not limited to:
- names
- identifiers
- dates
- values
- measurements
- architecture
- workflows
- processes
- diagrams
- configuration
- application state
- selected options
- menu paths
- terminal output
- dialog messages
- warnings
- alerts
- error messages
- trends
- comparisons
- anomalies
- relationships
- constraints
- exceptions
- evidence supporting the text
- evidence contradicting the text

---

IGNORE THE FOLLOWING
Do not describe images whose only purpose is decorative or branding.
Examples include:
- company logos
- organization logos
- product logos
- icons
- stock photography
- decorative illustrations
- marketing artwork
- generic office photos
- portraits
- page decorations
- backgrounds
- banners
- separators
- clip art
- watermarks
Ignore these unless they themselves convey operational, technical, analytical, legal, or contextual information discussed by the document.

---

DO NOT DESCRIBE
Avoid describing:
- colors
- clothing
- poses
- facial expressions
- artistic style
- image quality
- lighting
- camera angle
- composition
- aesthetics
- obvious visual facts

unless those details are directly relevant to understanding the document.
Examples of information that should usually NOT appear:
- "The image shows..."
- "The figure illustrates..."
- "The screenshot displays..."
- "A blue logo appears..."
- "There are several people..."
- "The background is white..."

---

OUTPUT RULES
If the image is classified as REDUNDANT:
Return an empty string.
Do not explain why.
Do not write:
"The image is redundant."
"The image adds no information."
Return absolutely nothing.

Otherwise:
Return exactly one concise paragraph.

Maximum 250 words.
Do not use bullet lists.
Do not use headings.
Begin immediately with the useful information.
Never begin with:
"The image shows..."
"The figure illustrates..."
"The screenshot displays..."
Focus only on information that contributes to understanding the surrounding document.
Do not repeat information already present in the surrounding text unless doing so is necessary to:
- explain a contradiction;
- clarify ambiguous information;
- connect newly discovered visual information to the document.

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
Tables are represented as JSON lines in the Markdown.
Each line is one row: {{"column_name": "cell_value", ...}}

STEP 1 — Diagnose the table using the image:
Compare the JSON lines against the table visible in the PDF image.

STEP 2 — Correct following these rules:

Case A — Keys look like real column headers (descriptive words, abbreviations):
Correct OCR errors in values and keys only.
Example fix: key "consulat" → "N° consulat" if the image shows "N° consulat".

Case B — Keys look like data values (country names, city names, raw numbers):
The header row was lost during extraction. The first data row was incorrectly used as column names.
1. Read the exact column names from the image (same language, same accents, same symbols).
2. Re-key every JSON object using the correct column names.
3. Check whether the row whose values match the current keys is missing from the data — if so, add it back.

FORBIDDEN in all cases:
Do not convert JSON lines to a Markdown table (| col | col |).
Do not reorder columns.
Do not remove data rows.
Do not add rows not visible in the image.

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

VLM_PROMPT_STAGE4_CHECK_PAGE_EN = """
You are an expert Markdown quality-control system.

INPUTS

You receive:
1. An image of PAGE {page_num} (out of {total_pages}) from the original PDF.
2. The Markdown corresponding to THIS PAGE ONLY.

TASK

This is a CORRECTION task, not a transcription task.
The provided Markdown is the source of truth for textual content.
The PDF image is used only to verify and correct existing Markdown.

Your goal is to produce a corrected version of the provided Markdown while preserving its content.
If the image is ambiguous or unreadable, preserve the existing Markdown unchanged.

---

PRIORITY ORDER

Apply these rules in the following order:

1. Preserve immutable content.
2. Preserve existing Markdown content.
3. Correct OCR mistakes.
4. Correct Markdown structure and formatting visible in the image.
5. Preserve pipeline-generated artifacts.
6. Ignore embedded screenshots and software interfaces.
7. Remove running headers, running footers, and page numbers.

Higher-priority rules always override lower-priority rules.

---

IMMUTABLE REGIONS

The following content is immutable.
Never modify these under any circumstances.
- Markdown links
Example:
[text](https://example.com)

- Bare URLs
Example:
https://example.com

- Link destinations
- Link anchor text
- Markdown image syntax

Example:
![caption](image.png)

- Pipeline markers
Example:
[[[IMAGE_DESC:1]]]
[[[IMAGE_DESC:2]]]

- Pipeline-generated image descriptions
Examples:
"The image shows..."
"L'image montre..."
"L'image illustre..."

- Inline code
Example:
`command`

- Fenced code blocks
Never:
- rewrite them
- recolor them
- restyle them
- reformat them
- fix OCR inside them
- change URLs
- change Markdown syntax
- change brackets or parentheses

Even if a hyperlink appears blue, underlined, bold, italic, or colored in the PDF image, preserve the Markdown exactly as provided.

---

CONTENT RULES

Never:
- Add new body text that does not already exist in the Markdown.
- Transcribe paragraphs that exist only in the image.
- Reconstruct missing paragraphs.
- Rewrite content.
- Summarize.
- Paraphrase.
- Change the meaning.

You may:
- Correct OCR mistakes.
- Correct Markdown formatting.
- Correct list hierarchy.
- Correct heading hierarchy.
- Correct table structure as described in the Tables section.

When uncertain, preserve the existing Markdown.
Do not guess.

---

ALLOWED OCR CORRECTIONS

You may correct only OCR-related mistakes, including:
- character substitutions
  - O ↔ 0
  - I ↔ l
  - rn ↔ m
  - cl ↔ d
- accents
- apostrophes
- quotation marks
- punctuation
- hyphens
- ligatures
- spacing
- duplicated characters
- missing characters clearly caused by OCR
- extraneous OCR characters

Do not modernize wording.
Do not rewrite sentences.

---

TEXT FORMATTING

Formatting must be determined independently of color.
Supported formatting:
Bold
**text**

Italic
*text*

Bold + Italic
***text***

Strikethrough
~~text~~

Restore formatting only when clearly visible in the image.

---

STYLE DETECTION RULES

Boldness is not color.
Darkness is not color.
Thickness is not color.
Text style and text color are independent attributes.
If both style and color exist, preserve both.
If uncertain about style:
Preserve the existing Markdown.

---

COLOR DETECTION

Apply color only when a clearly visible hue is present.
Allowed color names include:
red
blue
green
orange
purple
grey
brown
yellow

Apply color only to plain text.

Never apply color formatting inside:
- Markdown links
- URLs
- Markdown images
- HTML links
- inline code
- fenced code blocks
- pipeline markers
- image descriptions

Do not infer color from:
- boldness
- darkness
- shadows
- scan quality
- anti-aliasing
- compression artifacts

If uncertain whether text is colored:
Do not add color.

---

SYMBOLS
You may restore symbols that belong to the normal text flow, including:

!
ⓘ
➤
✓

Only when they replace OCR mistakes in existing Markdown.
Do not invent symbols.

---

EMBEDDED SCREENSHOTS

When inspecting the PDF image, ignore embedded software interfaces, including:
- application windows
- terminal screenshots
- legacy mainframe screens
- forms
- menus
- dialog boxes
- IDE screenshots

Examples:
GDAS01M
F1=AIDE
F3=MENU
Function-key bars
Ignore these elements only while inspecting the image.
Never remove existing Markdown because it resembles a screenshot.

---

PIPELINE ARTIFACTS

Pipeline-generated content must be preserved exactly.
Examples include:
[[[IMAGE_DESC:n]]]

Generated image descriptions beginning with:
"The image shows..."
"L'image montre..."
"L'image illustre..."

Never:
- remove them
- rename them
- rewrite them
- escape underscores
- modify spacing
- modify punctuation

---

TABLES

Tables are represented as one JSON object per line.
Example:
{{"Column":"Value","Column2":"Value2"}}
Never convert JSON tables into Markdown tables.
Never pretty-print JSON.
Never reorder columns.
Preserve one JSON object per line.

STEP 1
Compare the JSON table with the table visible in the PDF image.

STEP 2
Case A — Keys are genuine column headers.
Correct OCR mistakes in:
- keys
- values
only.

Case B — Keys are actually data values.
This indicates that the header row was lost.
Then:
1. Read the exact header names from the image.
2. Re-key every JSON object using those headers.
3. If the first data row became the keys during extraction, restore that row.

Do not invent rows.
Do not remove rows.
Do not reorder columns.
Do not convert to Markdown tables.

---

LISTS AND HEADINGS
Preserve the logical document hierarchy.
If a heading was extracted as a list item, convert it back into the appropriate heading.
If a list item was extracted as a heading, restore the list.
Only change structure when the image clearly supports it.

---

HEADERS, FOOTERS, PAGE NUMBERS
Remove only:
- running headers
- running footers
- page numbers

Examples:
Page 3 of 12
Page 6 sur 7
Do not remove legitimate section titles.

---

OUTPUT

Return only the corrected Markdown.
Do not include:
- explanations
- comments
- reasoning
- notes
- code fences

Output nothing except the corrected Markdown.
PAGE MARKDOWN (to correct):
{page_markdown}
"""


