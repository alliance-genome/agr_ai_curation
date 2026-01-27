# Batch Processing

Process multiple documents through Curation Flows automatically with real-time progress tracking and downloadable results.

## Overview

Batch Processing lets you run a saved Curation Flow against multiple documents at once, instead of processing each one individually. This is ideal when you have a set of papers and want to extract the same type of information from all of them.

**Key Benefits:**
- Process dozens of documents without manual intervention
- Real-time progress updates via Server-Sent Events (SSE)
- Download individual results or all results as a ZIP file
- Automatic error handling with per-document status tracking

## Starting a Batch Job

Batch processing is initiated from the **Documents** page, not from a separate batch page.

### Step 1: Select Documents

1. Navigate to **Documents** from the top navigation
2. Select the documents you want to process by clicking on them (checkboxes appear)
3. A selection bar appears at the bottom showing how many documents are selected
4. Click **"Start Batch"** to proceed to batch setup

### Step 2: Select a Flow

On the Batch page, you'll see your selected documents and a flow selector:

1. Choose from your saved Curation Flows in the dropdown
2. The system validates that your flow is compatible with batch processing:
   - Flow must contain a PDF input agent (to read from the selected documents)
   - Flow must end with a file output agent (CSV, TSV, or JSON formatter)
3. A green "Valid" message appears if the flow is compatible

**Don't have a saved flow?** See [Curation Flows](CURATION_FLOWS.md) to learn how to build and save flows.

**Need to change documents?** Click the **"Change"** button to return to the Documents page.

### Step 3: Start Processing

Click **"Start Batch"** to begin. The page switches to progress view where you can monitor in real-time.

## Monitoring Progress

### Real-Time Updates

Once processing starts, you'll see:

- **Progress Bar** - Shows documents processed vs total (e.g., "3 / 10" with percentage)
- **Document List** - Each document shows its current status with an icon
- **Audit Log** - Right panel shows detailed AI operations as they happen

### Document Status Icons

| Icon | Status | Meaning |
|------|--------|---------|
| Gray clock | Pending | Waiting to be processed |
| Blue spinning | Processing | Currently being processed |
| Green checkmark | Completed | Successfully processed, results available |
| Red X | Failed | An error occurred during processing |

### Live Streaming

Progress updates stream to your browser automatically - no need to refresh. The system uses Server-Sent Events (SSE) to push updates as they happen.

### Cancelling a Batch

Click **"Cancel Batch"** at the bottom of the progress panel to stop processing. Documents already completed keep their results; only pending documents are cancelled.

## Downloading Results

### After Completion

When the batch finishes, the page shows a completion summary:

- Number of successful documents (green chip)
- Number of failed documents (red chip, if any)

### Individual Downloads

Click the **download icon** next to any completed document to download its result file.

### Bulk Download (ZIP)

Click **"Download ZIP"** to get all completed results in a single ZIP file. The ZIP contains one result file per successfully processed document.

### Result Formats

Results are formatted according to your flow's output agent:
- **CSV Formatter** - Comma-separated values (opens in Excel, Google Sheets)
- **TSV Formatter** - Tab-separated values (for database import)
- **JSON Formatter** - Structured JSON data (for programmatic use)

## Recent Batches

The setup panel shows your **Recent Batches** (up to 5 most recent):

- Click any batch to view its details and results
- Each entry shows:
  - Flow name or batch ID
  - Status chip (running, completed, cancelled)
  - Document count (e.g., "8/10 docs")
  - Creation date

If you navigate to the Batch page while a batch is still running, it automatically resumes showing that batch's progress.

## Providing Feedback

For any document (processing, completed, or failed):

1. Click the **three-dot menu** (⋮) next to the document
2. Select **"Provide Feedback"** to report issues or suggestions
3. Select **"Copy Trace ID"** to copy the debugging trace ID

This automatically captures the AI's processing trace for developer review.

## Best Practices

### Preparing Documents

- **Verify document quality** - Ensure PDFs are text-searchable (not scanned images without OCR)
- **Test your flow first** - Run your Curation Flow on a single document in the regular chat before batch processing

### Optimal Batch Sizes

- **Small batches (1-10 documents)** - Good for testing and quick tasks
- **Medium batches (10-50 documents)** - Standard workflow
- **Large batches (50+ documents)** - Consider running during off-peak hours

### Handling Failures

If documents fail:
1. Check the error message shown below the document title
2. Common issues:
   - PDF extraction failures (corrupted or image-only PDFs)
   - Flow configuration issues (missing required agents)
   - API timeouts (temporary, may work on retry)
3. For persistent issues, use the feedback button to report to developers

## Starting a New Batch

After completing a batch, click **"Start New Batch"** to reset the page. Then navigate to Documents to select new documents.

## Example Workflow

Here's a complete example of batch processing gene expression data:

1. **Build a Curation Flow** ([see guide](CURATION_FLOWS.md))
   - PDF Agent → Gene Expression Agent → CSV Formatter
   - Save it with a name like "Gene Expression Extraction"

2. **Upload Documents**
   - Upload research papers through the Documents page

3. **Select and Start Batch**
   - Go to Documents page
   - Select all papers you want to process (checkboxes)
   - Click "Start Batch" in the selection bar
   - Select your "Gene Expression Extraction" flow
   - Click "Start Batch"

4. **Monitor Progress**
   - Watch the real-time progress bar
   - Review the audit log for detailed AI operations
   - Check for any failed documents

5. **Download Results**
   - Click "Download ZIP" for all CSVs
   - Or download individual files as needed
   - Import into Excel or your curation database

## Troubleshooting

### "No flows available"

You need to create and save a Curation Flow first. See [Curation Flows](CURATION_FLOWS.md).

### Flow validation fails

Your flow must:
- Contain a PDF input agent (to read from the selected documents)
- End with a file output agent (CSV, TSV, or JSON Formatter)

Flows that output to chat only cannot be used for batch processing.

### Documents stuck in "Processing"

- Check your network connection
- Refresh the page if the SSE connection dropped
- If persistent, the batch may have encountered a server issue - check with developers

### Download not working

- Ensure the batch has at least one completed document
- Check that your browser allows downloads from the site
- Try downloading individual files if ZIP download fails

## Related Documentation

- [Curation Flows](CURATION_FLOWS.md) - Build the flows used in batch processing
- [Available Agents](AVAILABLE_AGENTS.md) - Agents you can use in your flows
- [Getting Started](GETTING_STARTED.md) - Basic system usage
