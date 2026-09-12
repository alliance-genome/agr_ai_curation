# Batch processing

Run a saved flow against a set of papers when you want the same extraction and output settings for each one. Test the flow on a representative paper first; a completed batch does not mean every extracted answer is correct or ready for submission.

## Prepare a compatible flow

Create and save the flow in [Agent Studio → Flows](CURATION_FLOWS.md). It needs a PDF extraction step and at least one supported file output or **Curation Handoff**. A flow with only Chat Output cannot be used for a batch.

A custom extractor can be used when it has the required PDF extraction capability. The batch setup validates the selected saved flow and reports compatibility problems. You do not need an extra General PDF Extraction step before an extractor that already reads papers.

Configure output columns and instructions in the flow before starting. CSV, TSV, and JSON outputs format the flow's collected results. A Curation Handoff uses its supported review destination rather than promising a downloadable file.

## Start a batch

1. Open **Documents** and select the papers using their checkboxes. Wait for processing to finish for papers you have just uploaded.
2. Choose **Start Batch** in the selection bar.
3. Select your saved flow in batch setup and review the compatibility result.
4. Use **Change** if you need to select different documents.
5. Choose **Start Batch** to begin processing.

## Follow progress

The progress view shows the number processed, each document's status, and an audit panel with activity. Updates arrive automatically while the connection is active.

| Status | Meaning |
|--------|---------|
| Pending | Waiting to run |
| Processing | The flow is running for this paper |
| Completed | Processing finished; review its results |
| Failed | Processing encountered an error; read the message for that paper |

Choose **Cancel Batch** to stop the batch. Results already completed remain available. Cancellation can interrupt work in progress; review individual document statuses before deciding which papers to rerun.

## Review and download results

The completion view summarizes successful and failed documents. For file-producing flows, use a completed document's download action or **Download ZIP** for the available results together.

Review missing values, evidence associations, and unresolved validation findings before using the files. The output format follows your flow's formatter settings. A spreadsheet export does not establish that the records meet a database's submission requirements.

**Recent Batches** lets you reopen prior work and available results. Returning while a batch is running can resume its progress display. **Start New Batch** returns to setup for another batch.

## Report a problem

Use a document's **three-dot menu (⋮)** for **Provide Feedback** or **Copy Trace ID**. Include the flow, affected document, error or unexpected result, and what you expected instead. This keeps the report connected to the run's available trace information.

## Troubleshooting

**No flows are available.** Create and save a compatible flow first. An unsaved Workshop agent or flow draft cannot be selected as a saved batch flow.

**The selected flow is incompatible.** Read the validation message. Check that it contains a PDF-capable extractor and a supported file output or curation handoff, with valid source connections.

**A paper failed.** Read its error message. Check document processing status and the flow configuration. For a temporary service failure, retry the affected paper rather than repeating an entire successful batch. Report recurring failures with the trace ID.

**Progress appears stuck.** Check your connection and reopen the batch to see its current status before starting another run. If the server still reports it as processing without progress, send feedback to the development team.

**A download fails.** Confirm the flow produced a file for that paper. Try the individual download if ZIP download fails, and check whether the browser blocked downloads.

See [curation flows](CURATION_FLOWS.md) for output settings and [best practices](BEST_PRACTICES.md) for reviewing a trial run.
