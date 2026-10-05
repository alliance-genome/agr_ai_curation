# Importing curators' flows into the benchmark

Curators copy their own AI Curation flows into their benchmark account from the
benchmark portal (Flow fields, "Import from AI Curation"). Design:
`agr_ai_curation_benchmark_portal/docs/superpowers/specs/2026-10-05-flow-import-design.md`.

## Moving parts

| Where | Route | Auth |
|---|---|---|
| Main AI Curation | `GET /api/flow-exports`, `GET /api/flow-exports/{flow_id}?version=` | `Authorization: Bearer <ID token>` only, from the sign-in provider `AUTH_PROVIDER` selects; cookies ignored; audience must be in `FLOW_EXPORT_BEARER_CLIENT_IDS` |
| Benchmark resolver | `GET`/`POST /api/v1/benchmarks/flow-imports` | benchmark M2M + `X-Benchmark-Curator-Authorization` (read capability for `GET`, run capability for `POST`) |

Main signs each bundle (Ed25519, `aud=agr-ai-curation-flow-import`, 10 minutes by
default, bound to the curator's `sub` and issuer); the portal relays it; the resolver verifies
it with the public key and imports as that same curator. Copies get `uuid5` ids
(`backend/src/lib/flow_transfer/ids.py`), so a re-import updates the same flow in
place. Each import adds an append-only `benchmark_flow_imports` row.

## Configuration (all-or-none per side; a partial set stops startup)

| Side | Key | Notes |
|---|---|---|
| main | `FLOW_EXPORT_SIGNING_KEY` | SECRET. base64 of a 32-byte Ed25519 seed; one per environment |
| main | `FLOW_EXPORT_ISSUER` | the public AI Curation origin, e.g. `https://ai-curation-dev.alliancegenome.org` |
| main | `FLOW_EXPORT_BEARER_CLIENT_IDS` | the benchmark target client, `7fjbh0leu3lpuoglle4ufkphcr` |
| resolver | `FLOW_IMPORT_EXPORT_ISSUER` | the same origin as main's `FLOW_EXPORT_ISSUER` |
| resolver | `FLOW_IMPORT_EXPORT_PUBLIC_KEY` | base64 of the 32-byte public key; not secret |

The export bearer check takes its issuer and group claim from the platform sign-in
settings, exactly as the sign-in cookie does: with `AUTH_PROVIDER=cognito`, the pool
issuer and `cognito:groups`, requiring `token_use=id`; with `AUTH_PROVIDER=oidc`,
`OIDC_ISSUER_URL` and `OIDC_GROUP_CLAIM` (or the `config/groups.yaml` claim). Generic
OIDC has no `token_use`, so there a token carrying any access-token marker is refused
(`token_use` other than `id`, a Keycloak `typ` other than `ID`, a `scope`/`scp` claim,
or an RFC 9068 `at+jwt` header). Startup fails when the `FLOW_EXPORT_*` keys are set
but that issuer can't be resolved.

Size, count, page and signature limits are `FLOW_TRANSFER_*` settings in the
Operational limits section of `.env.example`. Main and the resolver both enforce the
bundle and signature limits, so keep those values the same on both sides.

Make a key pair without printing the seed (run as root on the host that keeps it):

```bash
umask 077
openssl genpkey -algorithm ed25519 -outform DER -out flow-export.der
tail -c 32 flow-export.der | base64 -w0 > flow-export.seed.b64      # FLOW_EXPORT_SIGNING_KEY
openssl pkey -inform DER -in flow-export.der -pubout -outform DER | tail -c 32 | base64 -w0; echo
```

Then delete the DER file (`shred -u flow-export.der`), and keep the seed file only
until it is copied into `.env` (then `shred -u flow-export.seed.b64`).

The last command prints only the public key. Rotation: replace both values and
restart both services; bundles in flight fail as `untrusted_bundle` and the
curator simply clicks again.

## Answers

Resolver: curator-fixable refusals are `200 {"outcome": "refused", "reason": <code>}`
with one of `model_unavailable`, `lookup_tools`, `step_unavailable`, `step_not_saved`,
`fields_need_choosing`, `flexible_output`, `too_large`, `cannot_run`; the portal
shows a fixed sentence for each. `400 untrusted_bundle|invalid_bundle` (including
non-finite numbers) and `409 import_conflict` are defects: each is one Sentry event
through the runtime facade (check code only) plus an ERROR log that is not promoted.
A committed import whose runnability report then fails is reported the same way and
still answers with its outcome (`runnable: null`).

- `413 bundle_too_large`: the request body or canonical bundle is over
  `FLOW_TRANSFER_BUNDLE_MAX_BYTES` (8 MiB by default).
- `503 flow_import_not_configured`: neither `FLOW_IMPORT_*` key is set.
- `503 flow_import_unavailable`: an unexpected failure, reported to Sentry.

Main: `401 authorization_required`, `404 flow_not_found`, `409 flow_changed` (the
flow moved past the requested `version`), `422 <reason code>` for a flow that can't
be imported, `503 authorization_unavailable` when the sign-in keys can't be read.
A flow whose bundle main itself can't build consistently is listed as `cannot_run`
(one broken flow never fails the list) and reported to Sentry as
`flow_export_inconsistent`.

- `503 flow_export_not_configured`: none of the three `FLOW_EXPORT_*` keys is set.
