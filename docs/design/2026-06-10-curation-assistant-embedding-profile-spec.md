# Curation-Assistant Embedding Profile — Implementation Specification

- **Date:** 2026-06-10
- **For:** SCRUM-6139 (centralized embedding store) — the `curation_assistant_v1` profile
- **Audience:** an engineer or coding agent implementing the centralized embedding pipeline who has **no prior knowledge** of AI Curation. This document is meant to be self-contained: follow it literally and you will produce embeddings the AI Curation assistant can reuse without modification.
- **Companion doc (rationale + decisions):** `2026-06-10-centralized-embedding-curation-assistant-requirements.md`
- **Source repo referenced throughout:** `agr_ai_curation` (AI Curation backend). Every algorithm below cites the exact `file:line` so you can check the real implementation.

---

## 0. How to read this document

This spec describes a 4-stage pipeline: **merged Markdown → elements → chunks → embeddings**, plus the **output schema** (parquet + ABC metadata table) and **acceptance tests**.

The single most important rule:

> **The vector is computed only from a chunk's `content` text. For AI Curation to reuse a vector, the chunk `content` we would have produced and the chunk `content` you produce must be byte-for-byte identical, and the embedding model must be identical.** Everything in Stages 1–3 exists to guarantee that byte-identical `content`. Get the text-normalization, element-splitting, and chunk-assembly rules exactly right, or the vectors silently land in a slightly different place and retrieval quality degrades without any error.

Because exact parity is fragile, **AI Curation offers to hand you the actual Python module** (the functions quoted below, packaged dependency-light) plus golden fixtures. You can implement from this spec or drop in our code; either way, the acceptance tests in §11 are the contract.

Two things AI Curation does on **its** side, so you do **not** implement them:
1. The **LLM section-hierarchy pass** (populates `parent_section`, `subsection`, `is_top_level`, `abstract_section_title`). Leave those fields null in your output; we fill them post-import. (They are metadata only — they never affect the vector.)
2. **Loading** the vectors+chunks into our Weaviate. You produce parquet + ABC metadata; we pull and load.

---

## 1. The pipeline at a glance

```
[ABC: converted_merged_main Markdown]              <-- Stage 1 input (you receive this)
        |
        v
  markdown_to_pipeline_elements()                  <-- Stage 2: parse markdown into typed "elements"
        |   (Title / NarrativeText / Table / ListItem, each with metadata)
        v
  chunk_parsed_document(strategy = by_title)        <-- Stage 3: assemble elements into chunks
        |   (target 1500 chars, 200 overlap; in-section oversized elements split,
        |    but a Title-seeded chunk can exceed 1500 — see §4.4)
        v
  embed each chunk.content with text-embedding-3-small (1536-d)   <-- Stage 4
        |
        v
  [parquet rows: chunk text + metadata + vector]  +  [ABC metadata table row(s)]   <-- output
```

Reference for the order in production: `backend/src/lib/pipeline/orchestrator.py` (parse → hierarchy → chunk → store). You implement the parse + chunk + embed; you skip hierarchy.

---

## 2. Stage 1 — Source text (input contract)

- **Input:** the **PDFX merged Markdown** that ABC stores as the `converted_merged_main` referencefile (`file_extension = md`, `file_publication_status = final`). Do **not** use NXML, TEI, or raw PDF text. (TEI must never be treated as canonical.)
- **Page markers must be preserved.** The chunker derives each chunk's page number purely from in-text page markers. If your markdown lacks them, every chunk gets `page_number = 1` and the assistant's PDF highlighting degrades to a whole-document scan. Acceptable marker forms (case-insensitive), each on its own line:
  - `<!-- page: N -->` (also `<!-- page = N -->`, `<!-- page N -->`)
  - `[page N]`
- **Text normalization dependency:** AI Curation normalizes text with `agr_abc_document_parsers.strip_markdown_formatting(...)` wrapped by `normalize_text` (see §3.3). `agr_abc_document_parsers` is the shared Alliance package ABC already uses (e.g. `validate_markdown`). **Use the same pinned version, `agr-abc-document-parsers==1.5.1`** (our current lock), so normalization matches exactly — `strip_markdown_formatting` behavior is part of the byte-identity contract.

---

## 3. Stage 2 — Markdown → elements

This is the exact production function. Reference: `backend/src/lib/pipeline/pdfx_parser.py:538-656`. Reproduce its behavior precisely.

### 3.1 The algorithm (verbatim)

```python
def markdown_to_pipeline_elements(markdown: str) -> List[Dict[str, Any]]:
    """Convert merged markdown output into pipeline element dictionaries."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    elements: List[Dict[str, Any]] = []
    section_path: List[str] = []
    current_page = 1
    index = 0
    i = 0

    heading_re = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    list_re = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.+)$")
    page_markers = [
        re.compile(r"^<!--\s*page\s*[:=]?\s*(\d+)\s*-->$", re.IGNORECASE),
        re.compile(r"^\[\s*page\s+(\d+)\s*\]$", re.IGNORECASE),
    ]

    def add_element(element_type, text, content_type, original_type):
        nonlocal index
        clean_text = normalize_text(text.strip())
        if not clean_text:
            return
        normalized_section_path = normalize_section_path(section_path)
        active_section = normalized_section_path[-1] if normalized_section_path else None
        doc_item_label = {
            "Title": "section_header",
            "ListItem": "list_item",
            "Table": "table",
        }.get(element_type, "paragraph")
        metadata = {
            "element_id": f"md_element_{index}",
            "doc_item_label": doc_item_label,
            "section_title": active_section,
            "section_path": normalized_section_path,
            "hierarchy_level": len(section_path) if section_path else 1,
            "page_number": current_page,
            "content_type": content_type,
            "original_type": original_type,
        }
        elements.append({"index": index, "type": element_type, "text": clean_text, "metadata": metadata})
        index += 1

    while i < len(lines):
        raw_line = lines[i]
        stripped = raw_line.strip()
        if not stripped:
            i += 1; continue

        # page marker -> updates current_page, emits nothing
        matched = False
        for pattern in page_markers:
            m = pattern.match(stripped)
            if m:
                current_page = max(1, int(m.group(1))); matched = True; break
        if matched:
            i += 1; continue

        # heading -> Title element, rebuilds section_path to this level
        hm = heading_re.match(stripped)
        if hm:
            level = len(hm.group(1))
            title = normalize_text(hm.group(2).strip())
            section_path = section_path[: level - 1]
            if title:
                section_path.append(title)
                add_element("Title", title, "heading", "markdown_heading")
            i += 1; continue

        # table -> consume consecutive lines starting with '|'
        if stripped.startswith("|"):
            table_lines = [stripped]; i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip()); i += 1
            add_element("Table", "\n".join(table_lines), "table", "markdown_table")
            continue

        # fenced code -> NarrativeText
        if stripped.startswith("```"):
            code_lines = [stripped]; i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1; break
                i += 1
            add_element("NarrativeText", "\n".join(code_lines), "code_block", "markdown_code_block")
            continue

        # list item (single line) -> ListItem
        lm = list_re.match(raw_line)
        if lm:
            add_element("ListItem", stripped, "list_item", "markdown_list_item")
            i += 1; continue

        # otherwise -> paragraph: consume until blank line or next block element
        paragraph_lines = [stripped]; i += 1
        while i < len(lines):
            peek = lines[i].strip()
            if not peek:
                i += 1; break
            if heading_re.match(peek) or peek.startswith("|") or peek.startswith("```") or list_re.match(lines[i]):
                break
            paragraph_lines.append(peek); i += 1
        add_element("NarrativeText", " ".join(paragraph_lines), "paragraph", "markdown_paragraph")

    return elements
```

### 3.2 Rules that are easy to get wrong

1. **Newlines normalized** to `\n` first (`\r\n`/`\r` → `\n`).
2. **Headings** rebuild `section_path`: a level-`L` heading truncates `section_path` to `L-1` entries then appends its title. `section_title` on every element = the **last** entry of the current path (the nearest heading). `section_path` = the full list.
3. **Paragraph assembly:** consecutive non-blank lines are joined with a **single space** (`" ".join`) and stop at a blank line, heading, table (`|`), code fence (```` ``` ````), or list item. (Note the list check uses `raw_line`/`lines[i]` — i.e. the original line — not the stripped peek.)
4. **Tables** join consecutive `|...` lines with `"\n"`. **Code fences** keep raw inner lines joined with `"\n"`, including the opening/closing ```` ``` ```` lines. Both become a single element.
5. **List items are one element per line** (no grouping of a list into one element).
6. **Empty after normalization → element dropped** (`add_element` returns early if `clean_text` is empty).
7. **`element_id` is positional**: `md_element_{index}`, where `index` only increments for emitted elements.

### 3.3 Text normalization (`normalize_text`)

Reference: `backend/src/schemas/pdfx_schema.py:96-122` plus `agr_abc_document_parsers.strip_markdown_formatting`.

```python
def normalize_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)          # 1. NFKC unicode normalization
    normalized = _LIGATURE_PATTERN.sub(_replace_ligature, normalized)   # 2. expand ff/fi/fl/ffi/ffl ligature codes
    normalized = _UNICODE_ESCAPE_PATTERN.sub(_replace_unicode_escape, normalized)  # 3. decode /uXXXX escapes
    return strip_markdown_formatting(normalized).strip()        # 4. strip markdown syntax, then trim

# constants (pdfx_schema.py:16-25)
_LIGATURE_REPLACEMENTS = {0: "ff", 1: "fi", 2: "fl", 3: "ffi", 4: "ffl"}
_LIGATURE_PATTERN = re.compile(r"/?uniFB0([0-4])", re.IGNORECASE)
_UNICODE_ESCAPE_PATTERN = re.compile(r"/u([0-9A-Fa-f]{4})")
```

`normalize_section_path` simply applies `normalize_text` to each path entry and drops any that become empty (`pdfx_schema.py:120-122`).

**This normalization is part of the byte-identity contract.** Reuse `agr_abc_document_parsers.strip_markdown_formatting` (pinned `==1.5.1`) rather than re-implementing markdown stripping.

### 3.4 Element output shape

Each element is a dict:
```python
{
  "index": int,
  "type": "Title" | "NarrativeText" | "Table" | "ListItem",
  "text": "<normalized text>",
  "metadata": {
    "element_id": "md_element_<index>",
    "doc_item_label": "section_header" | "list_item" | "table" | "paragraph",
    "section_title": "<nearest heading>" | None,
    "section_path": ["H1", "H2", ...],
    "hierarchy_level": int,
    "page_number": int,
    "content_type": "heading" | "list_item" | "table" | "paragraph" | "code_block",
    "original_type": "markdown_heading" | "markdown_list_item" | "markdown_table" | "markdown_paragraph" | "markdown_code_block",
  }
}
```

---

## 4. Stage 3 — Chunking (`by_title`)

Reference: `backend/src/lib/pipeline/chunk.py`. Strategy parameters (`backend/src/models/strategy.py:71-81`):

```
chunking_method      = by_title
max_characters       = 1500
overlap_characters   = 200
exclude_element_types = ["Footer", "Header"]   # see 4.1 — effectively a no-op for the markdown path
```

### 4.1 Pre-filter (important, and counter-intuitive)

`chunk_parsed_document` filters elements by **`doc_item_label == "page_footer"` only** (`chunk.py:125-129`):

```python
filtered_elements = [e for e in elements
                     if e.get("metadata", {}).get("doc_item_label") != "page_footer"]
```

The strategy's `exclude_element_types = ["Footer","Header"]` is **not** applied here. Since `markdown_to_pipeline_elements` never emits `doc_item_label == "page_footer"` (it only emits `section_header`/`list_item`/`table`/`paragraph`), **no elements are filtered out on the markdown path.** Implement it the same way: drop only elements whose `doc_item_label == "page_footer"` (there won't be any), and otherwise keep everything. Do **not** drop headings or anything else.

### 4.2 The `by_title` assembly (verbatim)

Reference: `chunk.py:183-271`.

```python
def _chunk_by_title(elements, strategy):
    chunks = []
    current_chunk = {"content": "", "elements": [], "metadata": {}}
    for element in elements:
        element_type = element.get("type", "")
        element_text = element.get("text", "")

        # Start a NEW chunk whenever a Title appears and the current chunk already has content
        if element_type == "Title" and current_chunk["content"]:
            chunks.append(current_chunk)
            overlap_text = current_chunk["content"][-strategy.overlap_characters:] if strategy.overlap_characters > 0 else ""
            current_chunk = {"content": overlap_text + element_text,
                             "elements": [element],
                             "metadata": dict(element.get("metadata", {}))}
        else:
            new_content = current_chunk["content"] + "\n" + element_text if current_chunk["content"] else element_text
            if len(new_content) > strategy.max_characters:
                if current_chunk["content"]:
                    chunks.append(current_chunk)
                overlap_text = current_chunk["content"][-strategy.overlap_characters:] if (strategy.overlap_characters > 0 and current_chunk["content"]) else ""
                combined_content = overlap_text + element_text
                if len(combined_content) > strategy.max_characters:
                    split_contents = _split_oversized_text(combined_content, strategy.max_characters, strategy.overlap_characters)
                    for split_content in split_contents[:-1]:
                        chunks.append({"content": split_content, "elements": [element], "metadata": dict(element.get("metadata", {}))})
                    current_chunk = {"content": split_contents[-1], "elements": [element], "metadata": dict(element.get("metadata", {}))}
                else:
                    current_chunk = {"content": combined_content, "elements": [element], "metadata": dict(element.get("metadata", {}))}
            else:
                current_chunk["content"] = new_content
                current_chunk["elements"].append(element)
                if not current_chunk["metadata"]:
                    current_chunk["metadata"] = dict(element.get("metadata", {}))
    if current_chunk["content"]:
        chunks.append(current_chunk)
    return chunks
```

Key points:
- A **Title** element forces a new chunk boundary (when the current chunk is non-empty). This is why chunk boundaries follow markdown headings.
- Within a section, elements are concatenated with a **single `"\n"`** until adding the next element would exceed `max_characters`.
- When a boundary is forced, the new chunk is **seeded** with `overlap_text` (the last `overlap_characters` characters of the chunk just closed) immediately followed (no separator) by the new element's text.
- The chunk's `metadata` is taken from the **first element** that seeded it.

### 4.3 Oversized-element split (verbatim)

Reference: `chunk.py:19-99`. Used when a single element (plus overlap) exceeds `max_characters`. It tries, in order: paragraph split (`\n\n`), sentence split (regex `(?<=[.!?])\s+`), then a hard split at the nearest space/newline within 200 chars of the boundary, carrying `overlap` characters between pieces.

```python
def _split_oversized_text(text, max_chars, overlap_chars=0):
    if len(text) <= max_chars:
        return [text]
    segments = []
    # 1) paragraph split on "\n\n"
    paragraphs = text.split("\n\n")
    if len(paragraphs) > 1:
        current = ""
        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) > max_chars:
                if current: segments.append(current)
                if len(para) > max_chars:
                    segments.extend(_split_oversized_text(para, max_chars, overlap_chars)); current = ""
                else:
                    current = para
            else:
                current = candidate
        if current: segments.append(current)
        if segments: return segments
    # 2) sentence split
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1:
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}" if current else sentence
            if len(candidate) > max_chars:
                if current: segments.append(current)
                if len(sentence) > max_chars:
                    segments.extend(_split_oversized_text(sentence, max_chars, overlap_chars)); current = ""
                else:
                    current = sentence
            else:
                current = candidate
        if current: segments.append(current)
        if segments: return segments
    # 3) hard split at a word boundary near max_chars, with overlap
    effective_overlap = min(overlap_chars, max_chars - 1) if overlap_chars > 0 else 0
    start = 0; text_len = len(text)
    while start < text_len:
        end = min(start + max_chars, text_len)
        if end < text_len:
            for k in range(end, max(start, end - 200), -1):
                if text[k] in (" ", "\n"):
                    end = k; break
        segment = text[start:end]
        if not segment:
            segment = text[start:min(start + max_chars, text_len)]; end = start + len(segment)
        segments.append(segment)
        if end >= text_len: break
        start = end - effective_overlap
        if start < 0: start = 0
    return segments
```

### 4.4 Chunk → record fields

After assembly, each chunk becomes a record. Reference for derivation: `chunk.py:473-613` (`_create_document_chunk`) + storage encoding `backend/src/lib/pipeline/store.py`.

- `chunk_index`: sequential 0-based, reassigned after all chunks built (`chunk.py:616-630`). Also drives the deterministic UUID.
- `content`: the chunk text exactly as assembled in §4.2 (this is what gets embedded).
- `element_type`: from the **first** element in the chunk — `Title→"Title"`, `Table→"Table"`, `ListItem→"ListItem"`, else `"NarrativeText"` (`chunk.py:503-512`).
- `page_number`: from the first element with a `page_number` (or provenance `page_no`); coerced to int ≥ 1, default 1 (`chunk.py:483-501`).
- `section_title`, `section_path`: from the chunk's seed-element metadata (markdown-derived; §3.4).
- `content_type`: from the seed-element metadata `content_type`.
- `char_count = len(content)`; `word_count = len(content.split())` (`chunk.py:587-596`).
- `has_table = any(element.type == "Table")`; `has_image = any(element.metadata.content_type in {"figure","image","picture"})` — always `False` for the markdown path (`chunk.py:570-575`).
- `chunking_strategy = "research"` (the strategy name).
- `parent_section`, `subsection`, `is_top_level`, `abstract_section_title`: **leave null** — AI Curation fills these via its LLM hierarchy pass post-import.
- `doc_item_provenance`: leave empty/null unless your source carries real bbox provenance (the markdown path normally has none).

> **`section_title` / `section_path` are pre-hierarchy values.** You emit the **raw markdown-derived** `section_title` (nearest heading) and `section_path`. In production these get **overwritten** by AI Curation's LLM hierarchy pass (e.g. `section_title` becomes `"Results > Strains"`) *before* our local chunk records are stored (`hierarchy_resolution.py:201-210`). That happens on our side post-import; it never changes `content` (only `element["text"]` is concatenated into `content`, never `section_title`). The golden fixtures (§11) are therefore generated from the **pre-hierarchy** element stream so they match what you produce.

> **Caution — chunks are NOT always ≤ 1500 chars.** The in-section path stops before exceeding `max_characters`, but the **Title-boundary seed** (`chunk.py:210-214`) prepends up to 200 overlap chars and appends the Title element's full text **without calling `_split_oversized_text`**. So a chunk whose seeding Title text is long can exceed 1500 — and even 1600. **Compute `content_preview` with the §6 truncation algorithm for every chunk; never assume `content_preview == content`.** (A 1499-char Title following a 1490-char paragraph yields a 1699-char chunk, whose preview is 1603 chars ending in `"..."` ≠ content.)

---

## 5. Stage 4 — Embedding

- **Model:** OpenAI `text-embedding-3-small`. **Dimensions: 1536, untruncated** (do **not** pass a `dimensions` parameter that shortens the vector). **dtype: float32.**
- **Embed only the chunk `content`.** Nothing else (no section title, no metadata) is embedded. In AI Curation, Weaviate's `text2vec-openai` module vectorizes only the `content` property; metadata is stored but `skip_vectorization=True` (`backend/main.py:136-160`).
- **Token preflight (match our guard):** before embedding, count tokens with `tiktoken.encoding_for_model("text-embedding-3-small")`. Our hard limit is `EMBEDDING_MODEL_TOKEN_LIMIT − EMBEDDING_TOKEN_SAFETY_MARGIN = 8191 − 500 = 7691` tokens (`store.py:75-94, 297-307`). A 1500-character chunk is ≈300–500 tokens, so this effectively never triggers — but implement the same check and fail loudly if a chunk ever exceeds it (it signals a chunking bug).
- **Determinism note:** OpenAI embeddings are effectively deterministic per (model, input), but not bit-identical across model minor revisions. Record the exact model string in the profile descriptor (§7) so a future model change is a new profile, never an in-place swap.

---

## 6. Output — per-chunk parquet schema

One row per chunk. Types are Arrow/parquet types. "Maps to" is the AI Curation Weaviate property (`store.py:340-359`) for reference.

| Column | Type | Required | Derivation | Maps to |
|---|---|---|---|---|
| `agrkb_reference_curie` | string | yes | doc-level (repeat per row or partition) | join key |
| `chunk_index` | int32 | yes | §4.4 | `chunkIndex` |
| `chunk_uuid` | string | yes | see §6.1 | Weaviate object id |
| `content` | string | yes | §4.2 | `content` (vectorized) |
| `content_preview` | string | yes | `content[:1600]`, and if `len(content) > 1600` then `content[:1600].rsplit(" ",1)[0] + "..."` (`store.py:317-319`) | `contentPreview` |
| `element_type` | string | yes | §4.4 (`Title`/`NarrativeText`/`Table`/`ListItem`) | `elementType` |
| `content_type` | string | optional | seed element `content_type` | `contentType` |
| `page_number` | int32 | yes | §4.4 (≥1) | `pageNumber` |
| `section_title` | string | yes (nullable) | seed element `section_title` | `sectionTitle` |
| `section_path` | list<string> | optional | seed element `section_path` | `sectionPath` |
| `char_count` | int32 | yes | `len(content)` | `metadata.character_count` |
| `word_count` | int32 | yes | `len(content.split())` | `metadata.word_count` |
| `has_table` | bool | yes | §4.4 | `metadata.has_table` |
| `has_image` | bool | yes | §4.4 (False here) | `metadata.has_image` |
| `chunking_strategy` | string | yes | `"research"` | `metadata.chunking_strategy` |
| `parent_section` | string | null | (AI Curation fills) | `parentSection` |
| `subsection` | string | null | (AI Curation fills) | `subsection` |
| `is_top_level` | string | null | (AI Curation fills; encode `"true"`/`"false"` if ever set) | `isTopLevel` |
| `doc_item_provenance` | string(JSON) | optional | normally null on markdown path | `docItemProvenance` |
| `embedding` | list<float32>[1536] | yes | §5 | the vector |

### 6.1 Deterministic chunk UUID

AI Curation derives a chunk's Weaviate UUID as (`store.py:121-136`):

```python
def generate_deterministic_uuid(document_id, chunk_index, content):
    unique = f"{document_id}:{chunk_index}:{content[:100]}"
    return str(uuid.UUID(bytes=hashlib.sha256(unique.encode()).digest()[:16]))
```

For the shared store, `document_id` is per-curator, so use a **tenant-independent** key instead:

```
chunk_uuid = uuid_from_sha256(f"{agrkb_reference_curie}:curation_assistant_v1:{chunk_index}:{content[:100]}")
```

Treat `chunk_uuid` as informational — on import AI Curation may re-derive its own UUID for its local namespace. What matters is that `chunk_index` and `content` are present so any UUID can be reproduced.

### 6.2 Encoding rules (match exactly)

- `content_preview`: the algorithm above (word-boundary trim + `"..."` only when truncating). Apply it to **every** chunk — it does **not** always equal `content` (Title-seeded chunks can exceed 1600; see §4.4).
- `is_top_level`: when AI Curation eventually sets it, it is stored as the **string** `"true"`/`"false"`, never a JSON boolean (`store.py:286-290`). Our section-filter tools do substring matches on it.
- **Enum fields are serialized strings:** `element_type` is the string value (`"Title"`, `"NarrativeText"`, `"Table"`, `"ListItem"`) and `chunking_strategy` is `"research"` — both models use `use_enum_values=True` (`strategy.py:25`, `chunk.py:117`). Emit the strings, not enum reprs.
- `embedding_timestamp` (our `embeddingTimestamp`) is set at store time as `datetime.now(timezone.utc).isoformat().replace("+00:00","Z")` (`store.py:355`). It is **not** part of chunk identity; our loader sets its own. You don't need to produce it.
- The AI Curation `metadata` Weaviate property is a JSON string of `{character_count, word_count, has_table, has_image, chunking_strategy, section_path, content_type, doc_items?}`. You can supply these as typed parquet columns (above); our loader assembles the JSON.

---

## 7. Output — profile descriptor + ABC metadata table

### 7.1 Profile descriptor (the "for the curation assistant" flag)

Carry this in the parquet file metadata **and** as columns in the ABC metadata table so consumers select exactly this set:

```
embedding_profile      = "curation_assistant_v1"
embedding_model        = "text-embedding-3-small"
embedding_dim          = 1536
embedding_dtype        = "float32"
chunker_name           = "by_title"
chunk_max_characters   = 1500
chunk_overlap_chars    = 200
source_text_kind       = "pdfx_merged_markdown"   # converted_merged_main
normalizer             = "agr_abc_document_parsers.strip_markdown_formatting"
normalizer_version     = "1.5.1"                   # agr-abc-document-parsers; part of byte-identity
schema_version         = "1.0.0"
```

Classifier and abstract-only embeddings are **separate profiles** (e.g. `abc_classifier_v1`, `abstract_v1`) in the same store, distinguished by `embedding_profile` + `embedding_model` + `schema_version`.

### 7.2 ABC metadata table (SCRUM-6141)

One row per `(reference, embedding_profile, schema_version)`:

| Column | Notes |
|---|---|
| `agrkb_reference_curie` / `reference_id` | ABC identity |
| `external_ids` | PMID / PMCID / DOI |
| `mod` | FB / WB / ZFIN / MGI / … |
| `embedding_profile` | `curation_assistant_v1` |
| `embedding_model`, `embedding_dim`, `embedding_dtype` | as §7.1 |
| `chunker_name`, `chunk_max_characters`, `chunk_overlap_chars` | as §7.1 |
| `source_file_class` | `converted_merged_main` |
| `converted_artifact_id` | ABC `referencefile_id` of the markdown |
| `source_md5` | md5 of the converted markdown |
| `chunk_count` | number of chunks/rows in the parquet |
| `parquet_s3_uri` | location of the parquet |
| `schema_version` | `1.0.0` |
| `created_at` | timestamp |
| `status` | e.g. `complete` |

**Recompute trigger:** when ABC re-converts a paper (new `converted_merged_main` / changed `source_md5`/`converted_artifact_id`), this profile must be recomputed and the row updated. (AI Curation detects staleness at import via these fields.)

---

## 8. The doc-level metadata AI Curation needs back

Carried once per reference (parquet doc columns and/or the ABC table):
- Identity + provenance: `agrkb_reference_curie`, `reference_id`, `external_ids`, `mod`, `converted_artifact_id`, `source_md5`, `file_extension`.
- (Optional) `abstract_section_title`, section hierarchy — but note AI Curation generates these itself via its LLM pass; you don't need to produce them. If you ever do run a hierarchy pass, mark which model produced it.
- (Optional, SCRUM-6140) a separate `abstract_embedding` (1536-d) + `abstract_text` — **additive**, not required by the assistant (we derive the abstract from chunks today).

---

## 9. Edge cases & gotchas (checklist)

1. **Page markers**: if absent, every chunk is page 1 → broken highlighting. Validate that your markdown has markers before embedding; surface a warning if a document yields all-page-1 chunks.
2. **Normalization parity**: use `agr_abc_document_parsers.strip_markdown_formatting` + NFKC + ligature/escape expansion. A different unicode form = a different vector.
3. **Separators**: paragraphs join with a single space; intra-section elements join with `"\n"`; tables/code join with `"\n"`. Don't "improve" these.
4. **No element filtering** on the markdown path beyond `doc_item_label == "page_footer"` (which never occurs). Do **not** drop headings.
5. **Overlap seeding** has no separator between `overlap_text` and the new element text.
6. **Oversized split** order is paragraph → sentence → hard-split; the hard-split looks back up to 200 chars for a space/newline.
7. **`content_preview`** does **not** always equal `content` — a Title-seeded chunk can exceed 1600 chars (see §4.4). Always compute it via the §6 truncation algorithm.
8. **`is_top_level`** is a string token when set, not a bool.
9. **Vector**: 1536-d float32, untruncated, from `content` only.
10. **Determinism**: identical markdown in → identical chunks + UUIDs out. Make the pipeline reproducible (no wall-clock or random in chunk/UUID derivation; `embeddingTimestamp` is the only time-dependent field and is not part of identity).

---

## 10. Reference index (verify against these)

| What | File:line |
|---|---|
| Markdown → elements | `backend/src/lib/pipeline/pdfx_parser.py:538-656` |
| `normalize_text` / `normalize_section_path` | `backend/src/schemas/pdfx_schema.py:96-122` |
| `strip_markdown_formatting` | `agr_abc_document_parsers` (external shared package) |
| Strategy defaults (`by_title`, 1500/200) | `backend/src/models/strategy.py:71-81` |
| Chunk dispatch + footer filter | `backend/src/lib/pipeline/chunk.py:107-181` |
| `by_title` assembly | `backend/src/lib/pipeline/chunk.py:183-271` |
| Oversized split | `backend/src/lib/pipeline/chunk.py:19-99` |
| Chunk record derivation | `backend/src/lib/pipeline/chunk.py:473-630` |
| Content preview / UUID / encoding / token preflight | `backend/src/lib/pipeline/store.py:121-136, 286-362` |
| Embedding/vectorizer config (model, only `content`) | `backend/main.py:136-160` |
| Pipeline order | `backend/src/lib/pipeline/orchestrator.py` |
| Env defaults | `.env.example:452-465`, `docker-compose.yml:185-189` |

---

## 11. Acceptance tests (the contract)

Parity is proven by golden fixtures, not prose. AI Curation will provide a set of `(input.md → expected_elements.json → expected_chunks.jsonl)` fixtures generated from the production functions. Your implementation passes if:

1. **Element parity:** `markdown_to_pipeline_elements(input.md)` equals `expected_elements.json` exactly (type, text, all metadata fields).
2. **Chunk parity:** the `by_title` chunker output equals `expected_chunks.jsonl` exactly on `content`, `chunk_index`, `element_type`, `page_number`, `section_title`, `section_path`, `char_count`, `word_count`, `has_table`, `has_image`.
   - `section_title`/`section_path` are the **raw markdown-derived** values; AI Curation generates the golden file from the **pre-hierarchy** element stream (its LLM pass overwrites these post-import — §4.4). These never enter `content`.
   - The fixture set **must include a Title-seeded chunk that exceeds 1500 chars** so the no-split seed path and the `content_preview` truncation are both exercised (see §4.4).
3. **Preview parity:** `content_preview` equals AI Curation's for every chunk, including oversized ones (computed via §6, not assumed equal to `content`).
4. **UUID parity:** `chunk_uuid` reproduces from `(key, chunk_index, content[:100])` per §6.1.
5. **Embedding sanity:** every `embedding` is length 1536, float32; cosine similarity between your vector and AI Curation's vector for the same `content` ≥ 0.9999 (ideally bit-identical).
6. **Round-trip:** AI Curation loads your parquet for a known paper and its retrieval/highlighting behave identically to a locally-uploaded version of the same PDF.

### Worked micro-example

Input markdown:
```
<!-- page: 1 -->
# Results
The *daf-16* gene was upregulated. We measured expression in N2 worms.

## Strains
- N2 (wild type)
- CB1370 daf-2(e1370)
```

Expected elements (abbreviated): `Title("Results", page 1, path=["Results"])`, `NarrativeText("The daf-16 gene was upregulated. We measured expression in N2 worms.", page 1, section_title="Results")` (note `*daf-16*` → `daf-16` after markdown stripping), `Title("Strains", page 1, path=["Results","Strains"])`, `ListItem("- N2 (wild type)", ...)`, `ListItem("- CB1370 daf-2(e1370)", ...)`.

Expected chunks (verified by running the real `_chunk_by_title`): everything is well under 1500 chars, so there are **two** chunks. The exact `content` strings (note the overlap seed glues `worms.Strains` with no separator, and intra-section joins use a single `\n`):

```
chunk 0 (index 0):
"Results\nThe daf-16 gene was upregulated. We measured expression in N2 worms."

chunk 1 (index 1):
"Results\nThe daf-16 gene was upregulated. We measured expression in N2 worms.Strains\n- N2 (wild type)\n- CB1370 daf-2(e1370)"
```

Here the previous chunk (76 chars) is shorter than the 200-char overlap window, so the entire chunk-0 content is prepended to chunk 1. `content` for each chunk is the exact concatenation per §4.2. AI Curation will ship the exact expected JSON for a fuller set of fixtures.
