# Batch Processing

Process multiple documents through Curation Flows automatically with real-time progress tracking and downloadable results.

## Overview

Batch Processing lets you run a saved Curation Flow against multiple documents at once, instead of processing each one individually. This is ideal when you have a set of papers and want to extract the same type of information from all of them.

**Key Benefits:**
- Process dozens of documents without manual intervention
- Real-time progress updates via Server-Sent Events (SSE)
- Download individual results or all results as a ZIP file
- Automatic error handling and retry capabilities

## Accessing Batch Processing

1. Navigate to the **Batch Jobs** page from the top navigation bar
2. You'll see two main sections:
   - **Create New Batch Job** - Start a new batch processing job
   - **Your Batch Jobs** - View and manage existing jobs

## Creating a Batch Job

### Step 1: Select a Flow

Choose from your saved Curation Flows. Only flows that have been saved (with a name) appear in the dropdown.

**Don't have a saved flow?** See [Curation Flows](CURATION_FLOWS.md) to learn how to build and save flows.

### Step 2: Select Documents

Select the documents you want to process:

1. Click **"Select Documents"** to open the document picker
2. Check the boxes next to documents you want to include
3. Click **"Confirm Selection"**

**Tips:**
- You can select documents from different upload batches
- Selected documents are shown as chips below the selector
- Click the X on a chip to remove a document from the batch

### Step 3: Name Your Batch (Optional)

Give your batch job a descriptive name to easily identify it later. If you don't provide a name, the system will generate one automatically.

### Step 4: Start Processing

Click **"Create Batch Job"** to begin. You'll be taken to the job details page where you can monitor progress in real-time.

## Monitoring Progress

### Real-Time Updates

The batch job detail page shows live progress:

- **Overall Progress Bar** - Shows how many documents have been processed
- **Status Indicator** - Current job status (pending, processing, completed, failed)
- **Per-Document Status** - See the status of each individual document

### Document Statuses

| Status | Meaning |
|--------|---------|
| **Pending** | Waiting to be processed |
| **Processing** | Currently being processed by the flow |
| **Completed** | Successfully processed, results available |
| **Failed** | An error occurred during processing |

### Live Streaming

Progress updates stream to your browser automatically - no need to refresh the page. The system uses Server-Sent Events (SSE) to push updates as soon as they happen.

## Downloading Results

### Individual Results

Click the **download icon** next to any completed document to download its results.

### Bulk Download

Click **"Download All Results"** to get a ZIP file containing all completed results. The ZIP file is named with the batch job ID and contains one result file per successfully processed document.

### Result Format

Results are formatted according to your flow's output agent:
- **CSV Formatter** - Comma-separated values
- **TSV Formatter** - Tab-separated values
- **JSON Formatter** - Structured JSON data

## Managing Batch Jobs

### Viewing Job History

The **Your Batch Jobs** section shows all your previous batch jobs with:
- Job name and ID
- Creation date
- Status (pending, processing, completed, failed, cancelled)
- Number of documents processed

### Cancelling a Job

To cancel a running batch job:
1. Click on the job to view details
2. Click the **"Cancel Job"** button
3. Confirm the cancellation

**Note:** Documents that have already been processed will keep their results. Only pending documents will be cancelled.

### Retrying Failed Documents

If some documents fail during processing:
1. View the batch job details
2. Failed documents are clearly marked
3. Click **"Retry Failed"** to reprocess only the failed documents

## Best Practices

### Preparing Documents

- **Verify document quality** - Ensure PDFs are text-searchable (not scanned images)
- **Test your flow first** - Run your Curation Flow on a single document to verify it works as expected before batch processing
- **Use descriptive names** - Name your batch jobs clearly so you can find them later

### Optimal Batch Sizes

- **Small batches (1-10 documents)** - Good for testing and quick tasks
- **Medium batches (10-50 documents)** - Standard workflow
- **Large batches (50+ documents)** - Consider running overnight or during off-peak hours

### Handling Errors

If a document fails:
1. Check the error message in the job details
2. Common issues include:
   - PDF extraction failures (corrupted or image-only PDFs)
   - Flow configuration issues
   - Temporary API timeouts
3. Use **"Retry Failed"** to reprocess after fixing any issues

## Example Workflow

Here's a complete example of batch processing gene expression data:

1. **Build a Curation Flow** ([see guide](CURATION_FLOWS.md))
   - PDF Agent → Gene Expression Agent → CSV Formatter

2. **Save the Flow**
   - Give it a name like "Gene Expression Extraction"

3. **Upload Documents**
   - Upload your research papers through the Documents page

4. **Create Batch Job**
   - Go to Batch Jobs
   - Select your "Gene Expression Extraction" flow
   - Select all papers you want to process
   - Name it "January 2026 Gene Expression Batch"
   - Click "Create Batch Job"

5. **Monitor Progress**
   - Watch the real-time progress bar
   - Check for any failed documents

6. **Download Results**
   - Click "Download All Results" for a ZIP of all CSVs
   - Import into Excel or your curation database

## Troubleshooting

### "No flows available"

You need to create and save a Curation Flow first. See [Curation Flows](CURATION_FLOWS.md).

### Documents stuck in "Processing"

- Check your network connection
- Refresh the page - SSE connection may have dropped
- If persistent, contact the development team

### All documents failed

- Test your flow on a single document first
- Check if the flow's output agent is configured correctly
- Review error messages for specific issues

### Download not working

- Ensure the batch job has at least one completed document
- Check that your browser allows downloads from the site
- Try downloading individual files if bulk download fails

## Related Documentation

- [Curation Flows](CURATION_FLOWS.md) - Build the flows used in batch processing
- [Available Agents](AVAILABLE_AGENTS.md) - Agents you can use in your flows
- [Getting Started](GETTING_STARTED.md) - Basic system usage
