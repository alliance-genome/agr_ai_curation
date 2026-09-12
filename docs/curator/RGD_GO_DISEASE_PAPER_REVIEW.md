# RGD GO and Disease Paper Review

This flow is available to authenticated RGD curators. It reviews one uploaded
paper for RGD GO recommendations and, when requested, disease assertions. It
does not submit annotations.

## Set up the saved flow once

1. Upload the PDF through **Documents → Upload Documents** and wait for
   processing to finish. Uploading starts document processing; you do not need to choose a processing profile.
2. In Agent Studio, create a flow from **RGD GO and Disease Paper Review**. Use
   **RGD GO Paper Review** when disease review is not wanted.
3. Save the created flow. In **Tools → Chat default**, choose **Flow**, select
   the saved flow, and save. The choice is the saved flow, not the package
   recipe.

The saved definition stays unchanged. Each ordinary chat message supplies the
current paper-review request.

## Start a review

Paste and edit this request in Chat:

```yaml
target_entities:
  - Cttn
  - MicroRNA-124-3p
include_other_genes: true
exclude_sections:
  - Introduction
  - Discussion
include_go: true
include_disease: true
require:
  - evidence_code
  - rationale
  - evidence_location
```

For the GO-only saved flow, set `include_disease: false` or omit that field.
Change the targets and options in later messages; do not edit or recreate the
saved flow.

## Review the result

- Review GO and disease results separately. The GO specialist applies RGD GO
  evidence policy; the disease extractor applies its own disease policy.
- Check the evidence code, rationale, and paper location against the Results,
  Methods, figures, and tables. Introduction and Discussion are excluded by the
  starter request.
- Treat unresolved gene-product identity, mature-RNA identity, ontology, or
  evidence-policy states as blockers requiring curator review. Do not infer a
  missing identifier or treat a suggestion as an accepted annotation.
- If `include_other_genes` is true, review additional genes independently of
  the named targets.

## Ask follow-ups

Ask a narrow question such as:

> What about GO:0005515 for Cttn in the prior review?

Keep the question in the same chat and refer to the prior review or displayed
result. Structured candidate and evidence result references are saved with the
flow output so the follow-up can inspect that review instead of beginning a new
broad paper extraction. Use a new starter request only when the targets or
review scope actually change.

## Troubleshooting

If a run times out, a service or tool fails, or an identity cannot be resolved, review the reported failure or unresolved result. Do not fill in a guessed identifier. Automatic routing and saved-flow runs have different runtime limits; include the run information when reporting a timeout.

If PDF processing or retrieval looks incomplete, check the document processing status before uploading it again. Report the document, flow run, and result reference through feedback. The development team can inspect the processing receipt for the methods used, completed stages, and timing.

To return to general routing, choose **Automatic** under **Tools → Chat
default**.

## Adapt the workflow

If you want to collect details outside the recipe's supported fields, ask Studio AI Chat to inspect the format first. A [custom extractor](CUSTOM_OUTPUT_STRUCTURES.md) can collect a different set of details, but it does not automatically reproduce this recipe's GO evidence policy or create submission-ready annotations.

Save agent and flow changes explicitly. Existing saved flows retain their selected agent revisions; changing a Workshop agent does not silently update a saved paper-review flow.
