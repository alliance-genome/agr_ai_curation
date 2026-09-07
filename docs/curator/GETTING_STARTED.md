# Getting started with AI Curation

Open [AI Curation](https://ai-curation.alliancegenome.org) and sign in with your account. If you are testing a development deployment, use the address provided by the development team. Available features and agents depend on the deployed version and your access.

## Open a paper and ask a question

1. Open **Documents** from the top navigation and upload your PDF.
2. Wait for document processing to finish, then open the paper in the chat workspace.
3. Ask a specific question, such as “Which Drosophila stocks were used in this study, and where did the authors obtain them?”
4. Review the answer and supporting passages in the paper.

An uploaded file may still be processing. Check its status before starting an extraction; uploading the same paper again does not fix an unfinished processing job.

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
