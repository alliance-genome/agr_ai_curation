# ABC Literature API Contract Verification

Status: live OpenAPI contract snapshot, verified 2026-06-25.

This note records the API surface AI Curation is allowed to use for the first
ABC Literature import path. It is intentionally narrower than the full
Literature service API. The 2026-06-26 ALL-359 clarification supersedes the
earlier blanket read-only rule for one case: AI Curation may request conversion
for an existing, authorized ABC reference discovered through MD5 or identifier
lookup.

See `conversion_handoff.md` for the ALL-359 decision about when that constrained
conversion request is allowed and how it relates to local PDFX.

## Verified OpenAPI Hosts

- Stage: `https://stage-literature-rest.alliancegenome.org/openapi.json`
- Production: `https://literature-rest.alliancegenome.org/openapi.json`

`https://literature-rest-stage.alliancegenome.org/openapi.json` did not resolve
from this workspace on 2026-06-25. Use the `stage-literature-rest` hostname for
stage checks unless Blue Team says otherwise.

Both reachable schemas reported:

- title: `Alliance Literature Service`
- version: `0.1.0`
- OpenAPI: `3.1.0`

The stage and production path sets matched for all AI Curation-relevant
endpoints. Path differences seen in quick diffs have been outside the AI
Curation allowlist.

## Allowed Endpoints

AI Curation normal import flows may call only these Literature endpoints:

| Purpose | Method and path | OpenAPI response contract |
| --- | --- | --- |
| PDF checksum match | `GET /reference/referencefile/by_md5/{md5sum}` | JSON array of `ReferencefileByMd5MatchSchema` |
| PMID/external curie lookup | `GET /reference/external_lookup/{external_curie}` | `ReferenceExternalLookupResponse` |
| Cross-reference lookup | `GET /reference/by_cross_reference/{curie_or_cross_reference_id}` | `ReferenceSchemaShow` |
| ABC/reference curie lookup | `GET /reference/{curie_or_reference_id}` | `ReferenceSchemaShow` |
| Reference search | `POST /search/references/` | request body `FacetsOptionsSchema`; response schema is currently unspecified (`{}`) |
| Reference file listing | `GET /reference/referencefile/show_all/{curie_or_reference_id}` | JSON array of `ReferencefileSchemaRelated` |
| File download | `GET /reference/referencefile/download_file/{referencefile_id}` | OpenAPI currently advertises `application/json` with an empty schema, so live fixture checks must verify actual bytes/content type |
| Existing-reference conversion request/poll | `GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false` | `ConversionStatusResponseSchema`; allowed only for an existing authorized reference/source match |

All endpoints in this allowlist are marked `HTTPBearer` in the current OpenAPI
schema. Health checks, smoke scripts, and provider clients should not assume
lookup/search/listing endpoints are public.

## Forbidden Endpoints

Normal AI Curation import flows must not call these endpoints:

- `POST /reference/referencefile/file_upload/`
- `POST /reference/add/`
- referencefile merge, bulk upload, or other Literature mutation paths

`conversion_request` is present in OpenAPI as `GET` with query parameters
`wait` and `overwrite_tei_md`. The endpoint can start provider-side conversion
work, so it must not be called from health checks, startup checks, or unknown
PDF/no-match uploads. It is allowed only after AI Curation has identified an
existing authorized ABC reference/source match. `overwrite_tei_md=true` remains
forbidden in this wave because it asks ABC to ignore and delete legacy
TEI-derived Markdown rows after successful replacement.

## Important Schema Findings

`ReferencefileByMd5MatchSchema` is the canonical shape for upload checksum
probes. Required fields include:

- `referencefile_id`
- `reference_id`
- `reference_curie`
- `display_name`
- `file_class`
- `file_publication_status`
- `file_extension`
- `md5sum`
- `is_annotation`
- `open_access`
- `referencefile_mods`
- `converted_referencefiles`

`converted_referencefiles` contains `ReferencefileConvertedDerivedSchema`
entries with:

- `referencefile_id`
- `display_name`
- `file_class`
- `file_extension`

Converted rows in the `by_md5` response do not carry MOD metadata. AI Curation
must continue inheriting access from the matched source/main PDF row.

`ReferencefileModSchemaRelated.mod_abbreviation` can be `null`. A null MOD row
on the source/main PDF may represent global/open access; a null MOD row on a
converted Markdown artifact must not be used as an entitlement source.

`show_all` returns file rows as `ReferencefileSchemaRelated`. These rows can
include `referencefile_mods`, but the first-cut import path should still derive
converted Markdown access from the source/main PDF when the source relationship
is known.

The allowlisted endpoints have `HTTPBearer` security in OpenAPI. Treat a live
`403` from `download_file` as authoritative. Do not persist, log, return, or
serialize any bearer credential used for Literature calls. When download access
is meant to represent the logged-in curator, the bearer credential must be
request-local/in-memory.

## Verification Commands

Use these from the repo root when checking the current OpenAPI schema:

```bash
mkdir -p /tmp/abc_literature_contract
curl -L -sS -m 20 https://stage-literature-rest.alliancegenome.org/openapi.json \
  -o /tmp/abc_literature_contract/stage-openapi.json
curl -L -sS -m 20 https://literature-rest.alliancegenome.org/openapi.json \
  -o /tmp/abc_literature_contract/prod-openapi.json

jq -r '.info | "title=\(.title) version=\(.version)"' \
  /tmp/abc_literature_contract/stage-openapi.json

jq -r '
  .paths as $paths |
  [
    "/reference/referencefile/by_md5/{md5sum}",
    "/reference/external_lookup/{external_curie}",
    "/reference/by_cross_reference/{curie_or_cross_reference_id}",
    "/reference/{curie_or_reference_id}",
    "/search/references/",
    "/reference/referencefile/show_all/{curie_or_reference_id}",
    "/reference/referencefile/download_file/{referencefile_id}",
    "/reference/referencefile/conversion_request/{curie_or_reference_id}"
  ][] as $p |
  ($paths[$p] // {}) | to_entries[] | select(.key | test("get|post")) |
  "\($p) \(.key|ascii_upcase) operationId=\(.value.operationId // "")"
' /tmp/abc_literature_contract/stage-openapi.json
```

Compare stage/prod path availability with:

```bash
diff -u \
  <(jq -r '.paths | keys[]' /tmp/abc_literature_contract/stage-openapi.json | sort) \
  <(jq -r '.paths | keys[]' /tmp/abc_literature_contract/prod-openapi.json | sort)
```

## Required Fixture-Backed Live Checks Before Endpoint Wiring

Before wiring upload or identifier import all the way through to download and
ingestion, run fixture-backed calls with known safe identifiers/checksums:

- `by_md5` for an unknown checksum returns an empty array.
- `by_md5` for a known source PDF returns a source row plus
  `converted_referencefiles` when converted Markdown exists.
- `by_md5` for a known restricted source returns source-PDF MOD metadata.
- `show_all` for a known reference returns the expected converted Markdown row.
- `download_file` for an authorized bearer credential returns Markdown bytes
  for the selected converted referencefile.
- `download_file` for an unauthorized bearer credential returns `403` and is
  treated as final.
- `external_lookup` resolves a PMID-like external curie.
- `reference/{curie_or_reference_id}` resolves an ABC/reference curie when
  identifier import starts from a provider-native reference identifier.
- `by_cross_reference` resolves DOI/PMCID only if fixtures prove those IDs are
  indexed consistently; otherwise keep DOI/PMCID unsupported in the API/UI.

Do not add normal CI tests that depend on live Literature services. Live checks
belong in gated/manual release evidence or a fixture-backed smoke script.

## Gated Live Smoke Test

The repo includes a manual pytest smoke for the checks above:

```bash
cd backend
ABC_LITERATURE_LIVE_ENABLE=1 \
ABC_LITERATURE_LIVE_BEARER_TOKEN="<authorized bearer credential>" \
ABC_LITERATURE_LIVE_KNOWN_MD5="<known source checksum with converted Markdown>" \
ABC_LITERATURE_LIVE_RESTRICTED_MD5="<known restricted source checksum with MOD metadata>" \
ABC_LITERATURE_LIVE_PMID="<known PMID or PMID:curie>" \
ABC_LITERATURE_LIVE_REFERENCE="<known AGRKB/reference curie or id>" \
ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID="<converted Markdown referencefile id>" \
ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN="<credential expected to receive 403>" \
ABC_LITERATURE_LIVE_RESTRICTED_REFERENCEFILE_ID="<restricted converted Markdown id>" \
python -m pytest tests/live_integration/test_abc_literature_live_smoke.py -q
```

Optional env vars:

- `ABC_LITERATURE_LIVE_BASE_URL`, default
  `https://stage-literature-rest.alliancegenome.org`
- `ABC_LITERATURE_LIVE_TIMEOUT_SECONDS`, default `20`
- `ABC_LITERATURE_LIVE_UNKNOWN_MD5`, default
  all-zero checksum fixture generated by the live-smoke runner

If `ABC_LITERATURE_LIVE_ENABLE` is not `1`, these tests skip. Individual
fixture-backed tests also skip when their fixture env vars are absent. The
OpenAPI contract test runs with only `ABC_LITERATURE_LIVE_ENABLE=1`.

## Durable Release Smoke Runner

For release evidence, prefer the repo-owned runner instead of hand-exporting
bearer tokens:

```bash
python3 scripts/testing/abc_literature_live_smoke.py --aws-profile ctabone
```

The runner uses boto3 with the selected AWS profile to create two short-lived
Cognito users, one authorized user with the configured Literature/MOD groups and
one unauthorized control with no groups. It obtains request-local bearer tokens,
runs the gated pytest harness above, writes non-secret evidence JSON, and
deletes both users in `finally`.

Default stage fixture:

- base URL: `https://stage-literature-rest.alliancegenome.org`
- reference: `AGRKB:101000000055784`
- PMID: `23970418`
- source checksum: configured with `ABC_LITERATURE_SMOKE_KNOWN_MD5`
- source PDF referencefile: `4040596`
- converted Markdown referencefile: `4672234`
- authorized Cognito groups: `FBStaff`, `FlyBaseCurator`

Evidence output defaults to:

```text
file_outputs/temp/abc_literature_live_smoke_<timestamp>.json
```

The evidence file records usernames, fixture IDs, pytest status, output tails,
and cleanup status. It must not contain bearer tokens, generated passwords, or
Cognito client secrets.

Useful overrides:

```bash
ABC_LITERATURE_SMOKE_AWS_PROFILE=ctabone
ABC_LITERATURE_SMOKE_USER_POOL_ID=us-east-1_d3eK6SYpI
ABC_LITERATURE_SMOKE_CLIENT_ID=<cognito app client id>
ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS=FBStaff,FlyBaseCurator
ABC_LITERATURE_SMOKE_EVIDENCE_DIR=file_outputs/temp
ABC_LITERATURE_SMOKE_PYTEST_TIMEOUT_SECONDS=180
ABC_LITERATURE_SMOKE_AWS_API_TIMEOUT_SECONDS=30
ABC_LITERATURE_SMOKE_EVIDENCE_TAIL_LIMIT=4000
```

If the Cognito app client requires a secret, provide
`ABC_LITERATURE_SMOKE_CLIENT_SECRET` through a local secret-bearing environment
file or shell only if the runner cannot discover it through
`describe-user-pool-client`. Do not commit it.

`--keep-users` exists only for debugging failed Cognito setup. Do not use it for
release evidence; retained-user runs are marked `debug_keep_users` and exit
nonzero.

## ALL-354 Closeout Decisions

As of 2026-06-26, the raw ABC Literature API contract is sufficient for the
first AI Curation cutover.

Verified and implemented contract:

- Stage OpenAPI: `https://stage-literature-rest.alliancegenome.org/openapi.json`.
- Production OpenAPI: `https://literature-rest.alliancegenome.org/openapi.json`.
- Both schemas expose the AI Curation endpoint set listed above.
- All allowlisted endpoints are `HTTPBearer` protected in OpenAPI.
- `by_md5` is the checksum preflight endpoint. Unknown checksums return `[]`;
  known checksums return source PDF metadata plus converted artifact metadata
  when available.
- `show_all` is the reference-level artifact refresh/listing endpoint.
- `download_file/{referencefile_id}` is the authoritative byte download and
  final server-side access gate.
- `reference/external_lookup/{external_curie}` is the first-cut PMID lookup
  path.
- `reference/{curie_or_reference_id}` is the first-cut provider-native
  AGRKB/reference lookup path.
- `conversion_request/{curie_or_reference_id}` is allowed only for an existing
  authorized source/reference match, with `wait=false` and
  `overwrite_tei_md=false`.

Fixture and test evidence:

- Live stage harness and durable runner verified OpenAPI, unknown `by_md5`,
  known restricted `by_md5`, PMID lookup, `reference/{curie}`, `show_all`,
  authorized `download_file`, and unauthorized `download_file` returning `403`.
- Canonical live fixture: reference `AGRKB:101000000055784`, PMID `23970418`,
  source checksum from `ABC_LITERATURE_SMOKE_KNOWN_MD5`, source PDF referencefile
  `4040596`, converted Markdown referencefile `4672234`, source MOD `FB`, and
  converted Markdown size `63756` bytes.
- Stage Cognito smoke users used `FBStaff` / `FlyBaseCurator` for authorized
  access and no groups for the unauthorized control.
- Fake ABC HTTP tests cover unfiltered `by_md5` and `show_all`, source
  `referencefile_mods`, converted child rows with null MOD metadata, authorized
  `download_file`, unauthorized `download_file` `403`, and the absence of
  create/upload client methods.
- Provider unit tests cover source-PDF null MOD rows mapping to global access
  and malformed/empty MOD payloads not being inferred as global.

Null-MOD/global decision:

- A null MOD on a source/main PDF row is the only case that may map to global
  access.
- A null MOD on a converted Markdown row is not an access grant. Converted
  Markdown inherits access from the matched source/main PDF.
- A live restricted fixture plus fake/unit coverage is enough for cutover; no
  additional live global fixture is required before ALL-354 closure.

DOI/PMCID/by-cross-reference decision:

- `GET /reference/by_cross_reference/{curie_or_cross_reference_id}` exists and
  remains in the provider allowlist.
- The ABC provider has unit coverage for DOI-style cross-reference resolution.
- The first cutover UI/API does not promise DOI, PMCID, FBrf, or other
  cross-reference imports because we do not yet have live fixture evidence that
  these identifier families are indexed consistently enough for curator-facing
  batch import.
- The backend identifier normalizer intentionally accepts only PMID/PubMed ID,
  AGRKB, and ABC identifiers in this cut. The Add Literature UI mirrors that
  promise and treats PMCID/FBrf as future expansion.
