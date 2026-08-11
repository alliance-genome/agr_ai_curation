# Horizontal Curation Amplify Prototype

This directory preserves the text source from the standalone horizontal curation
prototype deployed at:

- URL: https://main.dumm4mq0wvfug.amplifyapp.com/
- AWS Amplify app: `doug-horizontal-curation-prototype`
- App ID: `dumm4mq0wvfug`
- Amplify branch label: `main`
- Snapshot job: `0000000009`
- Deployment completed: 2026-07-14 00:29:12 UTC

## Provenance

The Amplify app is a manual `WEB` deployment. It has no connected repository,
commit identifier, or source Git branch. These files were downloaded from the
successful job 9 deployment artifact on 2026-08-11 and are preserved without
source changes.

The `main` value above is only the Amplify hosting branch label. It must not be
treated as proof that this prototype came from the repository's Git `main`
branch.

## Contents

- `index.html` — prototype structure and accessible controls
- `styles.css` — split-workspace and horizontal-grid presentation
- `app.js` — demo records and prototype interactions
- `public/favicon.svg` — deployed vector favicon
- `BINARY_ASSETS.sha256` — hashes for omitted binary demo assets

The deployed artifact also contains three rendered PDF-page JPEGs and a sample
publication PDF. They are intentionally not copied into this public source
reference because they are binary demo fixtures, not implementation source.
Their hashes are retained so a future agent can verify an authorized artifact
download.

## How to use this reference

Treat this as product/design evidence, not production code. The current React
application already owns the persistent PDF viewer, panel resizing,
PDF-to-form evidence navigation, autosave, validation APIs, and durable
candidate decisions. Port the horizontal review concepts through the existing
React, MUI, domain-envelope, and domain-pack contracts; do not copy the
prototype's hard-coded gene-expression records or its mock-only state changes.

Notable concepts represented here include:

- horizontally scrollable records and fields;
- a permanently visible identity/context column;
- optional pinned columns that move beside the identity column;
- a clear-pins action;
- compact and comfortable row density;
- field-level evidence, edit, and validate actions;
- a fixed row-validation action and global validation summary;
- keyboard-accessible resizing, pinning, and Shift-plus-wheel horizontal
  navigation;
- reduced-motion and responsive behavior.

## Retrieving the complete artifact

Authorized Alliance AWS users can retrieve job `0000000009` with the
`ctabone` profile through the Amplify `get-job` API. Use the expiring
`DEPLOY.artifactsUrl` returned by that request, download the ZIP, and compare
the binary files with `BINARY_ASSETS.sha256`. Never commit the expiring
presigned URL or its security token.
