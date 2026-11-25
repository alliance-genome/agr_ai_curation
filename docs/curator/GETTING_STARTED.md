# Getting started with AI Curation

Open [AI Curation](https://ai-curation.alliancegenome.org) and sign in with your account. If you are testing a development deployment, use the address provided by the development team. Available features and agents depend on the deployed version and your access.

## Open a paper and ask a question

1. Open **Documents** from the top navigation and upload your PDF.
2. Wait for document processing to finish, then open the paper in the chat workspace.
3. Ask a specific question, such as “Which Drosophila stocks were used in this study, and where did the authors obtain them?”
4. Review the answer and supporting passages in the paper.

An uploaded file may still be processing. Check its status before starting an extraction; uploading the same paper again does not fix an unfinished processing job.

## Work with supplemental files

Papers imported through **Documents → Add Literature** can include supplemental files alongside the main paper.

### Choose supplements when importing a paper

1. Open **Documents → Add Literature**, enter your identifiers under **Identifiers**, and click **Resolve**.
2. On a resolved paper, click **Choose supplements** to see its files. The main paper is always included; tick any supplemental files you want to import with it.
   - Supplements already converted by your configured literature source are ready to add right away.
   - When an import can request conversion, you may also tick an unconverted supplement. The import asks your literature source to convert it in the background while the main paper becomes usable without waiting; if conversion fails or times out, the main paper still finishes and **PDF Jobs** explains why the supplement was not added.
   - Files that cannot be added (such as unsupported file types, large data files, or text that did not pass readability checks) explain why without a checkbox.
3. Click **Import with 1 supplement** (or **Import with {n} supplements**) to start the import. To import only the main paper, leave supplements unticked and click **Resolve and Import**.
4. Follow per-file progress in the **PDF Jobs** panel below. Each supplement reports whether it is waiting for the main paper, waiting to be added, waiting for conversion, being added, **Added**, or **Not added** with the reason. You can leave the page once the import starts.

If a paper is already in your **Library**, Add Literature links you to it there so you can add its files instead of importing it again.

### Add converted supplements later from the Library

1. Open **Documents → Library**. The **Files** column shows how many ready files each paper holds.
2. Click **View Details** on a paper to open its details dialog. The **Files** section lists files already in the paper along with other files available from your configured literature source.
3. Tick any converted supplements you want to include and click **Add 1 file** (or **Add {n} files**).
   - Adding from the Library only offers files that your literature source has already converted; use **Refresh details** in the dialog header to check again later.
   - Files can be added only to papers imported from your literature source (not manually uploaded PDFs), and adding or removing is paused while an import or add job is running on the paper.
4. When the files finish adding while the details dialog is open, the paper's file list, the Library **Files** count, and any open PDF viewer for that paper refresh automatically. If you close the details dialog before the job finishes, reopen it later to check the result and refresh those views.

### Remove a supplement

1. Open the paper's details dialog from **Documents → Library** and click **Remove** next to a supplement in **Files**. (The main paper and a paper's only file cannot be removed individually.)
2. Review the confirmation dialog and click **Remove file**. AI Curation will no longer read the file or show it in the viewer, while evidence already saved stays intact. Any open PDF viewer and the Library **Files** count update automatically.
   - If the paper's newest extraction result quotes that file, the confirmation dialog warns that Review & Curate will ask you to run the extraction again.
   - A file quoted in an existing review session cannot be removed on its own; to remove it, delete the paper.

## Find your way around

The main workspace has a PDF viewer, chat, and a right panel with **Audit** and **Tools**.

| Area | What you can do |
|------|-----------------|
| PDF viewer | Read the paper and inspect linked evidence |
| Chat | Ask questions and review extraction or lookup results |
| Audit | Follow agent and tool activity for the current work |
| Tools | Run saved flows and choose the Chat default |
| Agent Studio | Browse agents, create a custom agent, and build flows with AI Chat |

The main chat carries out curation tasks. **AI Chat in Agent Studio** helps you configure agents and flows or understand how a result was produced.

## Look up biological information

You can ask database questions without uploading a paper. Include enough context to identify what you mean:

- “Look up the C. elegans gene daf-16 in Alliance records.”
- “Show existing GO annotations for human TP53 with experimental evidence.”
- “Resolve ‘nucleus’ as a GO cellular component term.”

With **Automatic** selected under **Tools → Chat default**, the system routes requests to available specialists. If you select a saved flow there instead, ordinary chat requests use that flow. Return to Automatic when you want general routing again.

See [available agents](AVAILABLE_AGENTS.md) for the main extraction and lookup tasks.

## Extract your own set of details

For a reusable custom extractor, open **Agent Studio → Agent Workshop → Custom data extraction**.

1. Name the type of item, such as **Stock**.
2. Describe what counts as one item and which details to collect, such as **Stock name** and **Source**.
3. Review the structure and the agent's settings, then **Save**.
4. Add the saved agent to a flow with a chat or file output, and save the flow.

You can ask AI Chat to guide you and propose changes throughout. **Apply** changes a draft; **Save** keeps the agent or flow in your account. The [custom output guide](CUSTOM_OUTPUT_STRUCTURES.md) explains fields, parts, and validators.

## Run and review a flow

Open a paper in the main chat, find your saved flow under **Tools**, and choose **Run**. Review the extracted records, evidence, and validation findings. Download files from the output cards if your flow has a file formatter.

Start with one paper before [running a batch](BATCH_PROCESSING.md). A successful run or a recognized identifier still needs curator review; custom records and ordinary CSV exports are not automatically ready for Alliance submission.

## Keep drafts and get help

Workshop and Flow Builder keep unsaved recovery drafts in this browser for your account. If you return to unfinished work, choose **Resume draft** or **Discard draft**. Use **Save** for account storage; browser recovery is not a substitute.

For an unexpected answer, use its **three-dot menu (⋮)** to send feedback or **Open in Agent Studio**. Describe the specific result and what you expected. For general configuration help, ask Studio AI Chat; **New chat** starts a fresh discussion without clearing your editor draft.

Continue with [Agent Studio](AGENT_STUDIO.md), [curation flows](CURATION_FLOWS.md), or [best practices](BEST_PRACTICES.md).
