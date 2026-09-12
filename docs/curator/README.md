# AI Curation: curator guide

Use AI Curation to read papers, extract information for review, and look up biological identifiers. You can use a ready-made agent or design a custom extractor for the details your curation task needs.

## Choose where to start

| I want to… | Guide |
|------------|-------|
| Upload a paper and ask a question | [Getting started](GETTING_STARTED.md) |
| Decide what to ask and assess the results | [Best practices](BEST_PRACTICES.md) |
| See which extractors and lookup agents are available | [Available agents](AVAILABLE_AGENTS.md) |
| Create or edit an agent, or get help from AI Chat | [Agent Studio](AGENT_STUDIO.md) |
| Extract my own set of details, such as stocks or reagents | [Custom output structures](CUSTOM_OUTPUT_STRUCTURES.md) |
| Save a sequence of extraction, validation, and output steps | [Curation flows](CURATION_FLOWS.md) |
| Run a saved flow on several papers | [Batch processing](BATCH_PROCESSING.md) |
| Use the RGD paper-review recipes | [RGD GO and disease paper review](RGD_GO_DISEASE_PAPER_REVIEW.md) |

## Make a custom extractor

Open **Agent Studio → Agent Workshop → Custom data extraction**. The wizard asks what kind of item you want to find in a paper, then which details to collect for each item. For example, you could define one record per fly stock, with a stock name and an optional source.

AI Chat can help throughout. Ask it to add details or parts, write instructions, attach compatible validators, or prepare a flow using the agent. Review its proposed changes before applying them. **Apply** updates the draft; **Save** saves it to your account.

## Use the right kind of output

**Custom Output Structure** gives your extractor a consistent set of fields. **Flexible extraction** lets the AI choose fields that may differ between runs. **Packaged domain format** uses an existing biological structure, with validation where supported and enabled. See [choosing an output mode](CUSTOM_OUTPUT_STRUCTURES.md#choose-an-output-mode) for the tradeoffs.

A flow also needs a way to present its results: **Chat Output**, CSV, TSV, or JSON. You can specify columns and formatting in the output step. Choosing CSV does not, by itself, define what the extractor collects or make the file ready for database submission.

## Review extraction and validation separately

Extraction records what the paper supports. Validation can resolve a gene, allele, or other supported value against a database or ontology. A validator attached to a field has not yet validated any answers: it runs when the agent processes data.

Review the evidence and any unresolved or ambiguous results. A valid identifier does not establish that an observation belongs in your curation. Final export or submission also depends on the selected data type's supported fields and readiness rules.

## Reuse shared agents and flows

Open **Agent Studio → Shared Library** to find work shared with your project. Workshop's **Open** dialog also includes accessible shared agents. Preview a teammate's agent or flow read-only, then clone it to make a private editable copy. Only the owner can change, delete, or revert an original. Sharing does not remove group restrictions.

Workshop's Tools section shows your requests and project-visible teammate requests, including ownership and status. Teammate summaries exclude private conversations and developer notes.

## Keep your work

Workshop and Flow Builder retain unsaved drafts on the current browser for your signed-in account. On returning, choose **Resume draft** or **Discard draft**. Use **Save** to keep work in your account; local recovery does not transfer between devices and can be lost if browser storage is cleared.

Saved flows keep their selected agent revisions. Updating an agent does not silently replace the version used by an existing flow.

## Ask for help or report a problem

Use AI Chat in Agent Studio for help with agents, prompts, fields, validators, and flows. Choose **New chat** in its header when you want a fresh conversation.

For a problem with an extraction result, use the response's **three-dot menu (⋮)** to send feedback or **Open in Agent Studio**. Include what you expected and the paper passage or result that needs attention. The linked conversation and available trace information help the development team investigate.
