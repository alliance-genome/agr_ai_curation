# RGD GO and Disease Paper Review

This flow is available to authenticated RGD curators. It reviews one uploaded
paper for RGD GO recommendations and, when requested, disease assertions. It
does not submit annotations.

## Set up the saved flow once

1. Upload the PDF through **Documents → Upload Documents** and wait for
   processing to finish. This is the normal high-accuracy PDF path: upload
   starts the full configured processing pipeline, with no profile choice.
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

## Bounded failures and troubleshooting

Automatic specialist calls have a default 15-minute deadline. A deadline,
provider failure, unavailable tool, or unresolved identity produces an explicit
failure or unresolved state; it must not produce guessed curation. Preferred
Flow runs are outside the Automatic specialist deadline and report their own
terminal success or failure.

If PDF processing or retrieval looks incomplete, check the document processing
status and its `pdf_processing_receipt`. The receipt records the selected
processing methods, stage outcomes, and timing when available. Report the
document, flow run, result reference, and receipt outcome with feedback; do not
repeatedly re-upload while a job is still running.

To return to general routing, choose **Automatic** under **Tools → Chat
default**.

## Production verification checklist

- Confirm an RGD curator can see both named recipes and a non-RGD account sees
  neither recipe nor the RGD GO specialist.
- Instantiate each recipe through Create Flow and confirm the result is an
  active, user-owned saved flow.
- Confirm only the saved flow UUID appears as the Flow chat-default choice.
- Upload a representative paper through the normal PDF path and confirm a
  completed processing receipt is available.
- Run the Cttn and MicroRNA-124-3p starter request. Confirm separately labeled
  GO and disease results, evidence locations, optional additional genes, and an
  unresolved mature-RNA state where identity cannot be established.
- Ask a same-chat result-reference follow-up and confirm it reuses the prior
  structured review.
- Revoke RGD membership and confirm discovery, execution, and the saved default
  become unavailable; reset the default to Automatic.
- Record the deployment version, flow UUID, flow-run/trace identifiers, and
  pass/fail outcome. Do not record curator credentials or paper contents.

Run real-provider or real-PDF release evidence only in an environment that has
passed the documented evidence-readiness preflight.
