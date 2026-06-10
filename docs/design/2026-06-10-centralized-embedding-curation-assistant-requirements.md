# Centralized Embedding Store — Requirements for the AI Curation Assistant

- **Date:** 2026-06-10
- **Status:** design assessment / response draft for SCRUM-6139
- **Author:** Chris Tabone (drafted with Claude)
- **Driving ticket:** [SCRUM-6139](https://agr-jira.atlassian.net/browse/SCRUM-6139) — *Store embeddings file in S3 with metadata stored in ABC tables* (Epic, reporter Ceri Van Slyke; technical lead Valerio)
- **Related child tickets:** SCRUM-5504 (spike, done), SCRUM-6137 (parquet format spike, done), SCRUM-6140 (small model to embed abstracts), SCRUM-6141 (ABC table for embedding metadata — *"store paragraph placement data (Chris T. requirement)"*), SCRUM-6142 (workflow to embed MD files), SCRUM-6143/6144/6145 (embed WB/FB/older training sets)
- **Related design docs in this repo:** `2026-05-28-abc-literature-document-ingestion-migration.md` (ABC literature source cutover), `2026-05-18-alliance-identifier-weaviate-index-design.md` (shared identifier index)
- **Blue Team prior art:** `alliance-genome/agr_alliance_embedding` (`docs/EMBEDDING_RESULTS.md`, `docs/EMBEDDING_ARTIFACTS.md`, `docs/EMBEDDING_METHODS_GUIDE.md`)

---

## 1. Purpose

Valerio and Ceri are building a centralized Alliance embedding store: embeddings written to S3 (parquet) with metadata tracked in ABC (Alliance Bibliography Central) Postgres tables, generated once when a reference enters ABC and reused by multiple consumers (ABC classifiers, abstract search, and the AI Curation assistant).

Today the AI Curation assistant works PDF-first: a curator uploads a PDF, we extract and chunk it, embed it into a per-user Weaviate tenant, and the agents retrieve evidence from it. The target architecture flips that: a curator types a **PMID / AGRKB reference curie**, we look the reference up in the central store, pull back **embeddings that were already computed**, load them, and the assistant is immediately ready — no per-curator upload, no per-curator re-embedding.

For that reuse to actually work, the centralized embeddings have to be produced **the same way the curation assistant produces them today**, or the vectors we pull back will live in a different vector space / have different chunk boundaries than what our retrieval stack expects, and recall will silently degrade.

This document is the precise specification of *what the AI Curation assistant needs* so Valerio's team can produce a "curation-assistant-profile" embedding set. It is the basis for our comment on SCRUM-6139.

> **One-line ask:** *Produce a distinct, flagged embedding profile (`curation_assistant_v1`) computed over the same source text (PDFX merged Markdown / `converted_merged_main`), with the same chunker (`by_title`, 1500 chars, 200 overlap, exclude Footer/Header), the same model (`text-embedding-3-small`, 1536 dims, untruncated), and carrying the per-chunk metadata listed in §5.4. Store the chunk text alongside the vector so we can either load vectors directly or re-embed if a profile drifts.*

---

## 2. TL;DR — what we need (the checklist for Valerio)

| # | Requirement | Why it matters |
|---|---|---|
| R1 | **Same source text:** chunk the PDFX **merged Markdown** (`converted_merged_main`), not raw NXML. | Chunk boundaries and text must match what our agents read and what fuzzy highlighting re-anchors against. NXML full-text produces different text/boundaries. |
| R2 | **Same chunking profile:** `by_title`, `max_characters=1500`, `overlap_characters=200`, exclude `Footer`/`Header`. | Our retrieval (hybrid + rerank + MMR) and our prompt budgets are tuned to ~1500-char section chunks. `word-window-600` windows are a different unit. |
| R3 | **Same embedding model + dims:** OpenAI `text-embedding-3-small`, **1536 dims, not truncated**. | A query is embedded with this model at retrieval time; pulled vectors only line up if they are in the identical space. (This also matches Blue Team's own pilot winner.) |
| R4 | **Per-chunk metadata** (full list in §5.4): `chunk_index`, `content`, `content_preview`, `element_type`, `content_type`, `page_number`, `section_title`, `section_path`, `parent_section`, `subsection`, `is_top_level`, `char_count`, `word_count`, optional `doc_item_provenance`. | These drive section-scoped retrieval tools, the PDF viewer, evidence display, and the agent's navigation. Missing fields silently break tools. |
| R5 | **Doc-level metadata:** `abstract_section_title`, LLM section hierarchy, `top_level_sections`, and source provenance IDs (AGRKB curie, reference_id, converted artifact id, md5, PMID/PMCID/DOI). | We need to map a centralized record back to an ABC reference and to the source file the viewer renders. |
| R6 | **Embedding profile flag** in both parquet and the ABC metadata table (e.g. `embedding_profile = curation_assistant_v1`, plus `model`, `dim`, `chunker`, `source_text_kind`, `schema_version`). | This is the *"flag that says these embeddings are for the AI curation assistant"*. It lets us pull exactly our set and ignore classifier/abstract-only profiles. |
| R7 | **Store the chunk text next to the vector** (don't store vectors alone). | Lets us (a) load vectors directly as BYO vectors *or* (b) re-embed locally if a profile is superseded, without re-fetching/re-chunking the paper. |
| R8 | **Placement = page number is mandatory; bbox is optional.** | We reconstruct highlight rectangles at view-time by fuzzy-matching chunk text against the live PDF (RapidFuzz). We do **not** need stored bbox, but we **do** need `page_number` per chunk and access to the source file. |

If R1–R3 can't all be met for the shared classifier/abstract profile, R7 is the safety valve: as long as the **chunk text** is in the store, AI Curation can re-embed it locally and still skip extraction + chunking + the LLM hierarchy pass.

---

## 3. How the AI Curation assistant embeds today (ground truth)

All claims below are from the current `main` of this repo, cited `file:line`.

### 3.1 Pipeline order

PDF upload → external extraction service → **merged Markdown** → Markdown parsed into "elements" → **LLM section-hierarchy resolution** → **chunking** → **embedding + storage in Weaviate**. Retrieval happens later at agent runtime.

Orchestrated in `backend/src/lib/pipeline/orchestrator.py` (parse → `resolve_document_hierarchy` at ~`:163-164` → chunk at ~`:183-185` → store).

### 3.2 Source text: PDFX merged Markdown (not NXML, not raw PDF text)

- PDFs are sent to the external **`agr_pdf_extraction_service`** (`grobid` + `marker`, consensus-merged), reached over HTTP by `backend/src/lib/pipeline/pdfx_parser.py` (`POST /api/v1/extract`, poll, then `GET .../download/merged`).
- The service returns **merged Markdown text**, not structured JSON elements. We convert that Markdown into pipeline element dicts locally via `markdown_to_pipeline_elements()` (`pdfx_parser.py:538-656`).
- Default extraction methods `grobid,marker`, merge enabled (`PDF_EXTRACTION_METHODS`, `PDF_EXTRACTION_MERGE`).

**Implication:** the unit of text the assistant actually embeds is **PDFX-merged Markdown**. Blue Team's benchmark embedded **NXML full-text**. These are different inputs and will not produce identical chunks even with the same chunker.

### 3.3 Markdown → elements

Each element produced by `markdown_to_pipeline_elements` carries (`pdfx_parser.py:555-585`):
`type` (`Title`/`Table`/`ListItem`/`NarrativeText`), `text`, and `metadata`: `element_id`, `doc_item_label`, `section_title`, `section_path`, `hierarchy_level`, `page_number`, `content_type`, `original_type`. Page markers (`<!-- page: N -->`, `[page N]`) advance `page_number`.

Note: because the source is merged Markdown (not docling JSON), elements generally **do not** carry bbox `provenance` — only `page_number`. See §3.8.

### 3.4 LLM section-hierarchy resolution

- Module: `backend/src/lib/pipeline/hierarchy_resolution.py`, runs **between parse and chunk** (best-effort; on failure pipeline continues flat).
- Model: env `HIERARCHY_LLM_MODEL` (default `gpt-5.4-mini`, reasoning `low`), via the OpenAI Agents SDK.
- Input: the unique section titles (in order) + a ~100-char preview each — **not** full text.
- Output (`HierarchyOutput`): per-header `parent_section`, `subsection`, `is_top_level`, plus a document-level `abstract_section_title`.
- Effect: each element/chunk gets `parent_section`, `subsection`, `is_top_level`, a concatenated `section_title` (e.g. `"Methods > Fly Strains"`), and `section_path`. Document-level hierarchy + `abstract_section_title` are persisted to Postgres `PDFDocument.hierarchy_metadata` (JSONB, `backend/src/models/sql/pdf_document.py:54`).

### 3.5 Chunking profile (production default)

`ChunkingStrategy.get_research_strategy()` (`backend/src/models/strategy.py:71-81`) — **the only strategy used for real uploads**:

```python
chunking_method = by_title
max_characters = 1500
overlap_characters = 200
include_metadata = True
exclude_element_types = ["Footer", "Header"]
```

- `by_title` starts a new chunk at each `Title` element and carries `overlap_characters` of trailing text forward (`backend/src/lib/pipeline/chunk.py:183-271`).
- Oversized elements are split recursively: paragraph → sentence → hard word-boundary split with overlap (`chunk.py:19-99`).
- Char-based, not token-based.
- (There is a separate `backend/src/lib/pdf_processing/strategies.py` with different numbers 2200/440, still exposed by the `/strategies` API route but **not** used by the runtime upload pipeline. Ignore it for production behavior.)

### 3.6 Embedding model + vectorization

- Model: env `EMBEDDING_MODEL`, default **`text-embedding-3-small`** (committed default in `docker-compose.yml:185`, `docker-compose.production.yml:193`, `.env.example:452`). **1536 dims.**
- Vectors are generated **server-side by Weaviate's `text2vec-openai` module** (Weaviate calls OpenAI), configured at app startup in `backend/main.py:136-160`:
  - `Configure.Vectorizer.text2vec_openai(model=text-embedding-3-small, vectorize_collection_name=False)`.
  - **Only `content` is vectorized.** `main.py` declares `content` plus the base properties (`documentId`, `chunkIndex`, `contentPreview`, `elementType`, `pageNumber`, `sectionTitle`, `metadata`, `docItemProvenance`, …) with `skip_vectorization=True`. The **section/hierarchy fields** `sectionPath`/`parentSection`/`subsection`/`isTopLevel`/`contentType` are **not** declared in `main.py`; they are added to the live collection by Weaviate **auto-schema** the first time `store.py` writes them (`backend/src/lib/pipeline/store.py:340-356`). Either way none of them are vectorized.
  - HNSW index; **multi-tenancy enabled**.
- The query is **also** embedded server-side at retrieval time (no manual vectors passed; see `backend/src/lib/weaviate_client/chunks.py` `hybrid_search_chunks`, "Weaviate embeds query server-side (V5)").

### 3.7 Storage + tenancy + retrieval

- Single `DocumentChunk` collection, **native Weaviate multi-tenancy, one tenant per user** (Cognito `sub`, hyphens→underscores; `backend/src/lib/weaviate_helpers.py:35-109`). The same PDF uploaded by two curators is embedded and stored twice, once per tenant.
- Deterministic chunk UUID = hash of `document_id:chunk_index:content[:100]` (`backend/src/lib/pipeline/store.py:121-136`) — so identical content collides on UUID **only within the same tenant**.
- Chunk properties stored: `documentId`, `chunkIndex`, `content`, `contentPreview`, `elementType`, `pageNumber`, `sectionTitle`, `sectionPath`, `parentSection`, `subsection`, `isTopLevel`, `contentType`, `docItemProvenance` (JSON string, often empty for the markdown path), `metadata` (JSON string: `characterCount`, `wordCount`, `hasTable`, `hasImage`, …).
- Retrieval (`hybrid_search_chunks`): hybrid search, `alpha=0.7` (70% vector / 30% keyword), `HybridFusion.RELATIVE_SCORE`, `initial_limit=25` → optional cross-encoder/Bedrock rerank → optional MMR diversification → `limit=10`. Short/symbol-like queries auto-enable a BM25-first fallback. Section-scoped tools filter by `sectionTitle`/`parentSection`/`subsection`.
- **Vectors live only in Weaviate.** Postgres stores file paths + `hierarchy_metadata` only; no vector copy in Postgres or S3.

### 3.8 Evidence placement is reconstructed at view-time (not stored)

This is the most important and least obvious fact for SCRUM-6141. We do **not** persist bbox rectangles for the markdown path. Instead, the PDF viewer **reconstructs** highlight placement on demand:

1. localize the right page with PDF.js find,
2. **fuzzy-align** the stored (noisy) chunk/quote text against the actual rendered page text using RapidFuzz (`backend/src/lib/pdf_viewer/rapidfuzz_matcher.py`, `MIN_FUZZY_MATCH_SCORE=70`),
3. recover and highlight the best matching span.

Design rationale in `docs/design/pdf-evidence-fuzzy-anchoring.md`: *"treat the stored quote as a noisy selector, not the literal text that must be highlighted."*

**Consequence for centralization:** "paragraph placement data" does **not** require stored bbox coordinates. It requires (a) `page_number` per chunk, and (b) that the **source file** the curator views (the original PDF, or its text) remains accessible at view time. If a centralized record carries real docling bbox provenance, great — we'll store it in `docItemProvenance` and it improves first-pass highlighting — but it is optional, and our fuzzy anchoring does not depend on it.

---

## 4. Blue Team's current pipeline vs. ours (alignment + divergences)

From `alliance-genome/agr_alliance_embedding`:

- **Corpus:** NXML full-text (`s3://agr-embedding-benchmark-dev/corpora/2026-05-nxml-pilot-v1/`).
- **Chunking:** two strategies — one `whole-paper` chunk + overlapping **`word-window-600`** chunks. The model comparison used `word-window-600`.
- **Models tested:** `text-embedding-3-small` (1536), `text-embedding-3-large` (3072), `blue-team-biowordvec-200`, `hash-128` (dry-run).
- **Artifacts:** `chunks.jsonl` + `embeddings/<model>.jsonl` per run in S3 (JSONL today; parquet is the SCRUM-6137 target).
- **Recommendation (1,000-paper pilot):** **use OpenAI small (`text-embedding-3-small`) as the default**; it won within-paper alias retrieval (the closest proxy to the curation use case) on genes/strains/transgenes. Large was marginally better on broad corpus recall + neighborhood locality.

| Dimension | Blue Team benchmark | AI Curation assistant (prod) | Aligned? |
|---|---|---|---|
| Source text | NXML full-text | PDFX **merged Markdown** (`converted_merged_main`) | **No** — different input |
| Chunk unit | `word-window-600` (+ whole-paper) | `by_title`, 1500 chars, 200 overlap, no Footer/Header | **No** — different boundaries |
| Section hierarchy | none | LLM-resolved (`gpt-5.4-mini`): parent/sub/is_top_level + abstract title | **No** — we need it |
| Embedding model | small / large / biowordvec compared | `text-embedding-3-small`, 1536, untruncated | **Yes** (their winner = our prod) |
| Vectors | precomputed, stored as JSONL/parquet in S3 | computed server-side by Weaviate `text2vec-openai`, stored in Weaviate | Mechanism differs (see §6) |
| Storage format | S3 JSONL → parquet | Weaviate multi-tenant | n/a |

**Bottom line:** the **model** is already aligned (and validated by their own benchmark). The **source text, chunk unit, and metadata** are not. The centralized store needs a curation-assistant-specific profile that reproduces our source + chunker + metadata, not just "the abstract embedding" or "the classifier embedding."

---

## 5. Detailed requirements

### 5.1 R1 — Source text

Embed the **PDFX merged Markdown** that ABC already stores as the `converted_merged_main` file (`file_extension=md`, `file_publication_status=final`). Per the ABC migration doc, file-source preference is `_nxml` → `_merged` → others, but **TEI (`_tei`) must never be treated as canonical**. For curation-assistant parity, prefer the same `converted_merged_main` the assistant would import via the ABC literature flow.

If the central store standardizes on a different source (e.g. NXML), then the curation-assistant profile must still be produced from the merged-markdown artifact, or we accept re-embedding (R7).

### 5.2 R2 — Chunking profile

Exactly: `by_title`, `max_characters=1500`, `overlap_characters=200`, exclude `Footer`/`Header`, char-based, with the oversized-element split fallback (paragraph→sentence→hard split, 200-char overlap). We can share our chunker code (`backend/src/lib/pipeline/chunk.py` + `markdown_to_pipeline_elements`) so the central pipeline produces byte-identical chunks. We're proposing to **lift `markdown_to_pipeline_elements` + the chunker into a shared library** (this is already flagged in the ABC migration doc as moving it out of `pdfx_parser.py`).

**Offer to Blue Team:** rather than have them reverse-engineer the behavior, we offer to deliver a **standalone, dependency-light Python chunker** (merged Markdown → elements → `by_title` chunks, exact 1500/200/exclude-Footer-Header semantics, emitting the §5.4 fields) plus **golden input/output fixtures** so both sides can assert identical chunk boundaries in CI. This is the cleanest way to guarantee R1+R2 parity and removes the single most likely cause of silent reuse failure (drifting chunk boundaries).

### 5.3 R3 — Model + dimensions

`text-embedding-3-small`, **1536 dimensions, untruncated** (no `dimensions` parameter override). Record the model string and dim in the metadata so a future model change is an explicit new profile, never an in-place swap.

### 5.4 R4 — Per-chunk fields (parquet columns)

One row per chunk. Required unless marked optional:

| Column | Type | Maps to our Weaviate property | Notes |
|---|---|---|---|
| `chunk_index` | int | `chunkIndex` | order within document |
| `content` | string | `content` | the embedded text (R7) |
| `content_preview` | string | `contentPreview` | first ~1600 chars, word-boundary trimmed |
| `element_type` | string | `elementType` | `Title`/`NarrativeText`/`Table`/`ListItem` |
| `content_type` | string *(optional)* | `contentType` | stored for parity; not currently read by retrieval/tools |
| `page_number` | int | `pageNumber` | **required** (placement, §3.8) |
| `section_title` | string | `sectionTitle` | concatenated path, e.g. `"Methods > Fly Strains"` |
| `section_path` | list<string> *(optional)* | `sectionPath` | stored for parity; not currently read by retrieval/tools |
| `parent_section` | string | `parentSection` | LLM-resolved top-level |
| `subsection` | string | `subsection` | LLM-resolved, nullable |
| `is_top_level` | string `"true"`/`"false"` | `isTopLevel` | **stored as a TEXT token, not a native bool** (`store.py:286-290`); section-filter tools `.like()` on it, so the load must stringify it |
| `char_count` | int | `metadata.characterCount` | |
| `word_count` | int | `metadata.wordCount` | |
| `has_table` | bool | `metadata.hasTable` | |
| `has_image` | bool | `metadata.hasImage` | |
| `doc_item_provenance` | json (nullable) | `docItemProvenance` | **optional**; bbox/page if docling provided it |
| `embedding` | list<float>[1536] | the vector | float32 |
| `chunk_uuid` | string | Weaviate object id | deterministic; see §5.6 |

The fields the assistant's agent tools / viewer actually **read** today are `content`, `page_number`, `section_title`, `chunk_index`, `parent_section`, `subsection`, `doc_items`, and the object `id` (e.g. `weaviate_search.py`, abstract derivation in `prompt_utils.py`). `content_type` and `section_path` are stored but not currently consumed — included above only so the profile is a faithful superset of what we persist; treat them as optional. **Encoding caveat:** `is_top_level` is persisted as the string `"true"`/`"false"` (not a JSON boolean), because the section-scoping tools filter it with a substring `.like()`; the central producer and our load path must agree on that string encoding or section filtering breaks silently.

### 5.5 R5 — Document-level fields

Carried once per reference (parquet doc columns and/or the ABC metadata table):

- Identity: `agrkb_reference_curie`, `reference_id`, `external_ids` (PMID, PMCID, DOI), `mod` (FB/WB/ZFIN/MGI/…).
- Source artifact: `source_file_class` (`converted_merged_main`), `converted_artifact_id` (ABC `referencefile_id`), `source_md5`, `file_extension`.
- Hierarchy: `abstract_section_title`, `top_level_sections`, full section hierarchy map, `hierarchy_model` (e.g. `gpt-5.4-mini`).
- Optional separate abstract embedding (SCRUM-6140/6141): `abstract_embedding` (list<float>[1536]) + `abstract_text` — but note this is **in addition to** chunk embeddings, not a replacement; the assistant currently derives the abstract from chunks (`fetch_document_abstract` in `prompt_utils.py`).

### 5.6 R6 — Embedding profile flag (the "for AI curation assistant" marker)

Both the parquet file and the ABC metadata table carry a profile descriptor so consumers pull only their set:

```
embedding_profile      = "curation_assistant_v1"
embedding_model        = "text-embedding-3-small"
embedding_dim          = 1536
embedding_dtype        = "float32"
chunker_name           = "by_title"
chunk_max_characters   = 1500
chunk_overlap_chars    = 200
exclude_element_types  = ["Footer","Header"]
source_text_kind       = "pdfx_merged_markdown"   # converted_merged_main
hierarchy_model        = "gpt-5.4-mini"
schema_version         = "1.0.0"
```

This is exactly the *"flag that says these embeddings are done for the AI curation assistant"* in the request. Classifier embeddings and abstract-only embeddings would be separate profiles (e.g. `abc_classifier_v1`, `abstract_v1`) in the same store.

Deterministic `chunk_uuid` recommendation for the shared store: hash of `agrkb_reference_curie : embedding_profile : chunk_index : sha1(content)` — stable across consumers and independent of per-user `document_id`.

### 5.7 R7 — Store chunk text with the vector

Do not store bare vectors. With `content` present we can:
- **(a) BYO-vector load:** push the precomputed vector into a Weaviate collection configured `vectorizer=none` and embed the query client-side (§6), or
- **(b) re-embed locally** if a profile is superseded or a consumer needs a different model — still skipping extraction, chunking, and the LLM hierarchy pass (the expensive parts).

### 5.8 R8 — Placement

`page_number` per chunk is mandatory; bbox is optional (we reconstruct via fuzzy anchoring at view-time and need the source file accessible, not stored coordinates). This *satisfies and slightly narrows* the "paragraph placement data (Chris T. requirement)" line in SCRUM-6141: page-level placement + section path is what we strictly need; bbox is a nice-to-have.

Two hard dependencies hide inside this:

- **Page markers must survive into the merged Markdown.** Our `page_number` is derived solely from page markers (`<!-- page: N -->` / `[page N]`) in the markdown (`pdfx_parser.py:550-553, 594-603`). If the centrally-produced markdown drops these markers, every chunk falls back to `page_number=1` and fuzzy anchoring degrades to a whole-document scan. So: the centralized chunking must run over markdown that **retains page markers**, and `page_number` must be populated.
- **The source file must be reachable at curation time** (the original PDF, or page text). Fuzzy anchoring (§3.8) matches chunk text against the rendered PDF; without it there is nothing to anchor to. This is a *dependency*, not just an open question — see §8 (open question 5).

---

## 6. Loading the centralized embeddings into AI Curation

Two integration shapes; we should pick one explicitly.

**Option A — BYO vectors (true reuse, no OpenAI cost at import):**
- Add/define a Weaviate collection with `Configure.Vectorizer.none()` and insert objects with the precomputed `vector=` from parquet.
- Change retrieval to embed the **query** client-side with `text-embedding-3-small` and use `near_vector`/hybrid-with-vector instead of the current server-side `text2vec-openai` query embedding.
- Pro: zero re-embedding; identical vectors. Con: this is **more than a config change** — be honest about scope. Every chunk retrieval funnels through `collection.query.hybrid(**query_params)` with server-side embedding (`chunks.py:612`), so it touches: (a) the collection vectorizer config in `main.py`, (b) a new client-side query-embed call, (c) passing `vector=` into `query.hybrid`, (d) MMR's `_vector` source (today it reads server-vectorized objects via `include_vector`, `chunks.py:565,679`), and (e) keeping the BM25/keyword paths working on the same collection. It must also avoid the **startup schema-migration footgun**: `main.py:198-207` will **delete and recreate** a collection whose vectorizer/multi-tenancy config differs from what's declared — a data-loss path that has to be handled deliberately. And dims/model parity (R3) must hold exactly or recall collapses.

**Option B — reuse text/chunks, re-embed locally (cheaper change, small OpenAI cost):**
- Pull `content` + metadata from the store, feed into the existing storage path; let Weaviate `text2vec-openai` embed as it does today.
- Pro: no retrieval-path change; we still skip extraction + chunking + hierarchy (the slow/expensive steps). Con: re-pays the embedding cost — but that cost is small (Blue Team's `EMBEDDING_COST_ESTIMATES`-class numbers put a clean 1,000-paper pass with `text-embedding-3-small` in the sub-dollar-to-low-dollar range; confirm the exact figure with Blue Team rather than treating it as fixed).

Recommendation: **start with Option B** (lowest-risk, immediate win — kills per-curator extraction/chunking/hierarchy and de-duplicates work across curators), and move to **Option A** once the profile is stable and we want to drop the embedding cost and per-tenant duplication entirely. Either way the central store needs R1–R6; Option A additionally needs the vector itself, Option B needs the chunk text (R7).

---

## 7. How this plugs into the ABC literature ingestion migration

The `2026-05-28-abc-literature-document-ingestion-migration.md` plan (epic KANBAN-1238) already moves AI Curation from PDF upload to **import-by-reference**: resolve a PMID/AGRKB curie → check ABC for converted Markdown → download → chunk → hierarchy → embed → store. That doc explicitly defers centralized embeddings to "a separate architecture change" and adds provenance columns specifically so embeddings can later be reused. **SCRUM-6139 is that separate change.** When both land, the import flow becomes: resolve reference → **fetch precomputed `curation_assistant_v1` embeddings from the central store** → load (Option A or B) → ready. The provenance columns from the migration (`source_provider_reference_curie`, `source_provider_converted_artifact_id`, `source_md5`, …) are the join keys to the central store.

---

## 8. Decisions & open questions

**Decided (Chris, 2026-06-10):**
- **Source = merged Markdown, not NXML.** The central store produces a markdown-based curation-assistant profile; it is not standardizing on NXML for our profile.
- **AI Curation runs the LLM section-hierarchy pass** (`gpt-5.4-mini`) on our side, so the central pipeline does not need to reproduce it.
- **Light load model on our side:** on import we pull the `curation_assistant_v1` embeddings + chunk text and load them into our Weaviate; everything else (canonical reference, files, dedup) stays in ABC. No new shared Weaviate collection is required for the first cut — embeddings are loaded into our store, the rest is deduplicated in ABC.
- **Recompute on re-conversion is assumed:** when ABC re-converts a paper, the curation-assistant profile is expected to be recomputed (necessary regardless); Blue Team owns that trigger.

**Open questions for Valerio / Blue Team:**
1. **Shared chunker:** we offer to write the standalone chunker + golden fixtures (§5.2) so both sides produce identical chunks — do you want us to deliver that, or own it from a spec we provide?
2. **Profile flag schema:** does the proposed ABC metadata table (SCRUM-6141) support multiple embedding profiles per reference (classifier / abstract / curation_assistant), keyed by `embedding_profile` + `model` + `schema_version`?
3. **Parquet granularity:** one parquet file per reference, or sharded multi-reference parquet with a `reference_curie` partition column? (Affects how we fetch a single paper at import time.)
4. **Vector dtype/precision:** float32 full precision (we need parity with query embeddings — no float16 truncation)?
5. **Source-file access at view-time (dependency, not optional):** for highlighting (§3.8) we require the original PDF (or its page text) reachable at curation time. Will the import flow proxy/cache the source PDF, or is it text-only? Treat a "yes" here as a hard precondition for the viewer, not a nice-to-have.
6. **Abstract embedding (SCRUM-6140):** is the separate abstract embedding intended for ABC classification/search only, or should the curation assistant consume it too? Today we derive the abstract from chunks; a dedicated abstract vector is additive, not required.

---

## 9. Verification appendix — how to connect and check everything

For an independent reviewer to confirm the claims in this doc:

**Repo / code (this checkout):** `/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation`
- Chunker + defaults: `backend/src/models/strategy.py:71-81`, `backend/src/lib/pipeline/chunk.py`.
- Markdown→elements + PDFX client: `backend/src/lib/pipeline/pdfx_parser.py:538-656`, `:45-73`.
- Hierarchy LLM: `backend/src/lib/pipeline/hierarchy_resolution.py`; default model `.env.example:529-530`.
- Embedding/vectorizer config: `backend/main.py:113-186` (real runtime), `backend/src/lib/weaviate_client/settings.py` (legacy demo).
- Storage + UUID + tenancy: `backend/src/lib/pipeline/store.py:121-136`, `backend/src/lib/weaviate_helpers.py:35-109`.
- Retrieval: `backend/src/lib/weaviate_client/chunks.py` (`hybrid_search_chunks`).
- Fuzzy placement: `backend/src/lib/pdf_viewer/rapidfuzz_matcher.py`, `docs/design/pdf-evidence-fuzzy-anchoring.md`.
- Env defaults: `grep -n EMBEDDING_MODEL docker-compose*.yml .env.example`.

**Jira (alliance-jira skill / curl):** re-read SCRUM-6139 and children (5504, 6137, 6140, 6141, 6142). Confirm SCRUM-6141 says *"store paragraph placement data (Chris T. requirement)"* and SCRUM-6140 references `agr_alliance_embedding/docs/EMBEDDING_RESULTS.md`.

**Blue Team repo (`gh`, GITHUB_ALLIANCE_TOKEN):**
`gh api repos/alliance-genome/agr_alliance_embedding/contents/docs/EMBEDDING_RESULTS.md -H "Accept: application/vnd.github.raw"` — confirm `text-embedding-3-small` recommendation, `word-window-600` chunking, NXML corpus.

**S3 (AWS profile `ctabone`):**
`AWS_PROFILE=ctabone aws s3 ls s3://agr-embedding-benchmark-dev/corpora/2026-05-nxml-pilot-v1/` and `.../runs/2026-05-nxml-pilot-1000-core-models-v1/` — confirm `chunks.jsonl` + `embeddings/<model>.jsonl` layout (the artifacts SCRUM-6137 proposes to move to parquet).

**Live Weaviate schema (optional, on the AI Curation EC2 / local docker):**
inspect the `DocumentChunk` collection config to confirm `text2vec-openai` + `text-embedding-3-small` + only `content` vectorized + multi-tenancy enabled.
