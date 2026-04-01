# 2026-04-01 PDF Evidence Fuzzy Search Experiments

## Purpose

Preserve the April 1, 2026 investigation into fuzzy matching between:

- quote-like text retrieved from Weaviate / chunked backend content
- the PDF.js searchable corpus for the same PDF

This document records the experiment setup, benchmark numbers, important caveats, and the most promising matcher directions that emerged from the work. It exists so the fuzzy-search thread is not lost while related investigations continue elsewhere.

## Context

The working problem was:

- the backend frequently provides a sentence-like quote that is close to the paper text
- the PDF viewer ultimately has to highlight text using PDF.js page text
- many quote failures appear to come from representation drift rather than total absence of related content

The user wanted the benchmark to focus on realistic quotes a curator might actually click, not arbitrary whole chunks.

## Inputs Used

### Source document

- PDF: `sample_fly_publication.pdf`
- Document ID in the live sandbox benchmark: `64fa682e-a074-446c-821e-c4a605d102f0`

### Quote source

- Live chunk data from the Symphony sandbox backend for the sample fly paper
- Quotes sampled from narrative sections such as `Introduction`, `Methods`, and `Results and Discussion`

### PDF-side text source

- Real PDF.js page text / searchable corpus extracted via local probe utilities
- Not reconstructed chat text, not OCR guesses, not DOM overlay text

## Utilities Built

### Probe utility

`[scripts/utilities/pdfjs_find_probe.mjs](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdfjs_find_probe.mjs)`

Purpose:

- load the sample PDF through `pdfjs-dist`
- expose page-level PDF.js search text
- expose real `PDFFindController` behavior
- compare raw query text against PDF.js page corpus
- inspect whitespace-collapsed alignment behavior

### Quote benchmark

`[scripts/utilities/pdfjs_quote_benchmark.mjs](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdfjs_quote_benchmark.mjs)`

Purpose:

- sample realistic sentence-window quotes from the live chunk corpus
- run them against the real PDF.js corpus
- record literal hits, whitespace-collapsed hits, and failure classes

### Python matcher bakeoff

`[scripts/utilities/pdf_text_matcher_bakeoff.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdf_text_matcher_bakeoff.py)`

Purpose:

- compare candidate fuzzy substring matchers against the same 100-quote set
- use a silver reference span for evaluation
- measure page localization, span overlap, and runtime

## Key Artifacts Produced

These files were used as the main temporary reports during the experiment:

- `/tmp/pdf-page-corpus.json`
- `/tmp/pdf-quote-benchmark-100.json`
- `/tmp/pdf-text-matcher-bakeoff-100.json`
- `/tmp/pdf-quote-benchmark-100-refreshed.json`
- `/tmp/pdf-text-matcher-bakeoff-100-refreshed.json`

## Benchmark Methodology

### Quote sample

- Realistic quote-like passages only
- `100` sampled quotes from the live backend chunk corpus
- Sample biased toward curator-relevant narrative text rather than giant chunks

### Baseline measurement

Each quote was tested against the PDF.js corpus for:

- literal presence
- sanitized literal presence
- normalized literal presence
- whitespace-collapsed presence
- PDF.js controller "found" behavior

### Failure analysis

The benchmark also attempted to classify non-literal failures into coarse buckets such as:

- punctuation / formatting drift
- high-overlap wording drift
- boundary whitespace drift
- markdown / wrapper drift
- partial overlap / chunk rewrite

### Matcher bakeoff

The Python bakeoff compared:

- exact literal search
- `RapidFuzz` partial ratio alignment
- `edlib` HW alignment
- `fuzzysearch`

The bakeoff used a silver reference span:

- literal PDF.js span when available
- otherwise the best diagnostic candidate span known at the time

This is useful for comparison, but it is not hand-labeled gold truth.

## Probe Findings

### The problem is text-to-text matching

The practical matching problem is:

- input A: markdown-ish quote text from retrieval / chunking
- input B: PDF.js searchable page text
- task: find the best matching span in B for A

The problem is therefore well-framed as fuzzy substring localization over text, even though the texts come from different upstream representations.

### Not all token boundaries collapse

For the long `crb` quote on page 8, the probe showed:

- literal PDF.js match: no
- whitespace-collapsed match: yes
- query whitespace boundaries preserved in PDF match: `39`
- query whitespace boundaries collapsed in PDF match: `11`
- extra whitespace in PDF where quote had none: `1`
- collapse rate for that quote: `0.22`
- `allQueryWhitespaceBoundariesCollapsed`: `false`

Important takeaway:

- PDF.js text is not simply "all spaces removed"
- boundary drift is selective and clustered around identifiers / citations such as `crb 11A22`, `crb 8F105`, `crb p13A`, and `males [58]`

### Direct `_pageContents` slicing looked unsafe

Early probe inspection showed that `pageMatches` / `pageMatchesLength` offsets did not line up cleanly with direct substring boundaries in `_pageContents`.

Representative examples from the investigation:

- a clean title query had literal query index `44` in `_pageContents`, while the selected raw start was `42`
- a page-8 `crb` excerpt had literal query index `5863`, while the selected raw start was `5790`

This strongly suggested that using `pageMatches` and `pageMatchesLength` as direct slice boundaries for rebuilding a canonical quote fragment was not trustworthy.

## 100-Quote PDF.js Baseline

From `/tmp/pdf-quote-benchmark-100.json`:

```json
{
  "totalQuotes": 100,
  "pdfjsControllerFound": 55,
  "literalPresent": 52,
  "sanitizedLiteralPresent": 3,
  "normalizedLiteralPresent": 0,
  "recoveredBySanitizedOrNormalized": 3,
  "whitespaceCollapsedPresent": 20,
  "boundaryOnlyFailures": 4,
  "hardFailures": 41,
  "controllerFoundWithoutLiteral": 3,
  "offsetSliceMismatchCount": 52
}
```

### Failure classifications

```json
{
  "punctuation_or_formatting_drift": 28,
  "high_overlap_text_drift": 11,
  "boundary_whitespace_drift": 4,
  "markdown_or_wrapper_drift": 3,
  "partial_overlap_or_chunk_rewrite": 2
}
```

### Difference signals

```json
{
  "token_content_equal_but_spacing_or_punctuation_blocks_literal_match": 19,
  "lexical_substitution": 24,
  "spelling_variant_or_inflection": 6,
  "identifier_or_token_boundary_collapse": 4,
  "extra_function_words_in_pdf": 3,
  "missing_function_words_in_pdf": 2,
  "markdown_or_search_wrapper_removed": 3
}
```

### Interpretation of the baseline

The 100-quote run made several things clear:

- exact / native literal search alone is not sufficient
- simple sanitization helps a little, but not much
- whitespace-insensitive structure is present more often than literal structure
- a meaningful fraction of the failures involve real wording drift, not just punctuation drift
- the current PDF.js-selected offset data should not be treated as a safe source for rebuilding canonical quote text

## Python Fuzzy Matcher Bakeoff

From `/tmp/pdf-text-matcher-bakeoff-100.json`:

### Exact

```json
{
  "page_match_rate": 0.52,
  "match_count": 52,
  "average_duration_ms": 0.0227
}
```

### RapidFuzz partial ratio alignment

```json
{
  "page_match_rate": 1.0,
  "match_count": 100,
  "nonliteral_reference_page_match_count": 48,
  "reference_coverage_mean": 0.9891,
  "average_duration_ms": 2.1030
}
```

### edlib HW

```json
{
  "page_match_rate": 0.98,
  "match_count": 98,
  "nonliteral_reference_page_match_count": 46,
  "reference_coverage_mean": 0.9937,
  "average_duration_ms": 2.2686
}
```

### fuzzysearch

```json
{
  "page_match_rate": 0.97,
  "match_count": 97,
  "nonliteral_reference_page_match_count": 45,
  "reference_coverage_mean": 0.9936,
  "average_duration_ms": 68.4703
}
```

### Non-literal subset only

For the `48` non-literal silver-reference cases:

- exact: `0/48` page matches, mean reference coverage `0.0`
- RapidFuzz: `48/48` page matches, mean reference coverage `0.9792`
- edlib: `46/48` page matches, mean reference coverage `0.9474`
- fuzzysearch: `45/48` page matches, mean reference coverage `0.9266`

### Misses on the non-literal subset

- `RapidFuzz`: `0` page misses
- `edlib`: `2` page misses
- `fuzzysearch`: `3` page misses

The known misses for `edlib` and `fuzzysearch` included the difficult partial-rewrite cases, including the `These observations support applying this analysis ...` quote.

## Span Quality Versus Page Localization

One important nuance from the bakeoff:

- `RapidFuzz` was best overall at finding the correct page / neighborhood
- `edlib` sometimes produced slightly tighter span boundaries when it succeeded
- `fuzzysearch` was substantially slower while still missing more cases

This means there are two separate concerns:

- page localization / neighborhood recall
- boundary quality of the returned substring

The experiments strongly favored `RapidFuzz` for the first concern.

## Most Important Preserved Takeaways

### 1. Fuzzy substring localization is viable

The experiment strongly supports treating this as a fuzzy text-to-text matching problem between:

- quote-like backend text
- canonical PDF.js page text

This is not merely a geometry problem or a PDF rendering problem.

### 2. Page-local fuzzy matching looks promising

The strongest pattern from the experiments was:

- exact literal search can get some cases directly
- many failures still land near the right page / neighborhood
- a bounded fuzzy matcher over page text can recover much more of the difficult set

### 3. RapidFuzz looked like the strongest first matcher to try

Reasons:

- highest page-match rate in the bakeoff
- no misses on the non-literal subset in the silver benchmark
- fast enough to be practical in iterative diagnostics

### 4. edlib remains attractive for alignment quality

Reasons:

- very competitive runtime
- sometimes cleaner boundaries than `RapidFuzz`
- still a strong candidate even though it missed the hardest partial-rewrite cases

### 5. `fuzzysearch` was clearly weaker for this use case

Reasons:

- much slower than the other two serious fuzzy candidates
- lower recall on the harder cases

### 6. Do not trust PDF.js selected offsets as canonical quote slices

The experiments repeatedly suggested that:

- selected PDF.js offsets and lengths may be fine for PDF.js internal highlighting
- they are not a safe basis for reconstructing canonical quote text by direct substring slicing

## Post-PDFX Refresh Rerun

After the PDF extraction service was improved, the benchmark was rerun against refreshed chunk text for the same sandbox document.

### Refresh procedure

- The benchmark document in the sandbox backend was reprocessed with `force_reparse=true`
- The first reprocess attempt failed chunk verification because stale chunks were still present in Weaviate
- Existing chunks for the document were manually cleared
- Reprocess was run again, which produced a clean `51`-chunk replacement set

This means the refreshed rerun used:

- the same PDF.js corpus from the sample PDF
- newly refreshed quote-like text from the updated PDFX-backed chunk corpus

### Refreshed 100-quote PDF.js baseline

From `/tmp/pdf-quote-benchmark-100-refreshed.json`:

```json
{
  "totalQuotes": 100,
  "pdfjsControllerFound": 73,
  "literalPresent": 67,
  "sanitizedLiteralPresent": 0,
  "normalizedLiteralPresent": 0,
  "recoveredBySanitizedOrNormalized": 0,
  "whitespaceCollapsedPresent": 25,
  "boundaryOnlyFailures": 6,
  "hardFailures": 21,
  "controllerFoundWithoutLiteral": 6,
  "offsetSliceMismatchCount": 67
}
```

### Refreshed failure classifications

```json
{
  "punctuation_or_formatting_drift": 24,
  "boundary_whitespace_drift": 6,
  "high_overlap_text_drift": 2,
  "partial_overlap_or_chunk_rewrite": 1
}
```

### Refreshed difference signals

```json
{
  "token_content_equal_but_spacing_or_punctuation_blocks_literal_match": 16,
  "identifier_or_token_boundary_collapse": 6,
  "lexical_substitution": 14
}
```

### What improved after refreshing the quote corpus

Relative to the earlier 100-quote run:

- literal presence improved from `52` to `67`
- PDF.js controller found improved from `55` to `73`
- hard failures dropped from `41` to `21`
- high-overlap wording-drift failures dropped from `11` to `2`
- markdown / wrapper drift disappeared from the refreshed failure buckets

This strongly suggests that better upstream quote text materially improves the benchmark before any viewer-side matcher changes are applied.

### Refreshed Python matcher bakeoff

From `/tmp/pdf-text-matcher-bakeoff-100-refreshed.json`:

#### Exact

```json
{
  "page_match_rate": 0.67,
  "match_count": 67,
  "average_duration_ms": 0.0218
}
```

#### RapidFuzz partial ratio alignment

```json
{
  "page_match_rate": 1.0,
  "match_count": 100,
  "nonliteral_reference_page_match_count": 33,
  "reference_coverage_mean": 0.9938,
  "average_duration_ms": 2.1696
}
```

#### edlib HW

```json
{
  "page_match_rate": 0.99,
  "match_count": 99,
  "nonliteral_reference_page_match_count": 32,
  "reference_coverage_mean": 0.9962,
  "average_duration_ms": 2.2757
}
```

#### fuzzysearch

```json
{
  "page_match_rate": 0.99,
  "match_count": 99,
  "nonliteral_reference_page_match_count": 32,
  "reference_coverage_mean": 0.9962,
  "average_duration_ms": 72.9933
}
```

### Interpretation of the refreshed rerun

The refreshed rerun changed the picture in a useful way:

- the benchmark became much less dominated by upstream text rewrite drift
- the remaining misses skewed more toward punctuation, formatting, and token-boundary collapse
- exact search became noticeably more competitive
- `RapidFuzz` still remained the strongest overall first-choice matcher
- `edlib` remained highly competitive, especially for span quality

The refreshed results therefore strengthen two conclusions at once:

- upstream extraction quality matters a lot
- even after upstream improvement, fuzzy substring localization still offers meaningful value beyond literal search

## Caveats

### Silver labels, not gold labels

The bakeoff did not use hand-labeled gold truth for every non-literal quote.

For non-literal quotes, the benchmark used the best known diagnostic span available at the time. That is enough to compare methods meaningfully, but it is not the final word on exact span quality.

### Single-paper benchmark

All reported quotes came from a single paper:

- `sample_fly_publication.pdf`

This was intentional for tight iteration, but broader validation is still needed across multiple PDFs and extraction styles.

### Upstream text quality was not held constant

The benchmark intentionally used real retrieved quote-like text. That means some failures may reflect:

- backend chunk drift
- extraction-service merge drift
- markdown wrappers
- PDF.js representation drift

This is a feature of the benchmark rather than a bug, but it means the results mix multiple upstream effects together.

## What This Document Is Preserving

The main preserved conclusion is:

- exact native PDF.js search is not enough for this problem class
- a real fuzzy substring matcher is likely required
- among the tested candidates, `RapidFuzz` and `edlib` emerged as the strongest options
- `RapidFuzz` was the best overall first choice in the 100-quote benchmark

## Related Files

- `[scripts/utilities/pdfjs_find_probe.mjs](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdfjs_find_probe.mjs)`
- `[scripts/utilities/pdfjs_quote_benchmark.mjs](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdfjs_quote_benchmark.mjs)`
- `[scripts/utilities/pdf_text_matcher_bakeoff.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/pdf_text_matcher_bakeoff.py)`
- `[scripts/README.md](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/README.md)`
- `[pdf-evidence-fuzzy-anchoring.md](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/docs/design/pdf-evidence-fuzzy-anchoring.md)`
