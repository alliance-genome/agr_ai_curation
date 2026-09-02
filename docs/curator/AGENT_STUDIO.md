# Agent Studio Guide

Agent Studio helps you understand how the AI curation agents work and gives you tools to improve them. You can browse agent prompts, build visual curation workflows, and chat with Claude Opus about any of it.

## Accessing Agent Studio

Click **"Agent Studio"** in the navigation bar at the top of the application.

## What You'll Find

Agent Studio has three main tabs: **Agents**, **Flows**, and **Agent Workshop**. The tabs sit on the left. The Claude chat sits in a panel on the right, and it is available from every tab.

### Claude Chat (Right Panel)

The panel on the right is your chat with Claude Opus. You can ask Opus about whatever you are viewing on the left - agent prompts, flows, or your workshop draft.

You can make more room for your work:

- Click **Hide Claude** in the chat header to shrink the panel to a narrow strip on the right edge. Click **Show Claude** on that strip to bring it back. A small orange dot on the strip means Claude answered while the panel was hidden.
- Press **Ctrl+.** (or **Cmd+.** on a Mac) to hide or show Claude from anywhere in Agent Studio.
- Drag the divider between the tabs and the chat to change how much space each side gets. Your choice is remembered.
- On a narrow browser window, the panel is replaced by a **Claude** button at the right end of the tab bar. Click it to open the chat as a slide-out sheet. Press **Escape**, click outside the sheet, or click **Close Claude** to put it away.

When you click **Discuss with Claude** on an agent or **Verify with Claude** in a flow, the chat opens by itself if it was hidden.

When your question is about how the application itself works, Opus can inspect the live repository in read-only mode to verify whether a feature, restriction, or code path exists before answering.

Opus is best used as an explanation and drafting assistant. It can help you
interpret prompts, domain-envelope metadata, validation choices, trace context,
and flow structure. It does not replace the curation workspace readiness checks:
final export and submission still depend on the saved envelope, validation
findings, and domain-pack policy.

### Agents Tab

Browse the instructions given to each AI agent and chat with Opus about them.

**Agent Browser (Left Panel)**

See all agent prompts organized by subcategory:
- **System** - Supervisor Agent that routes your queries to specialists (internal, not available in Flow Builder)
- **PDF Extraction** - PDF Extraction Agent and Gene Expression Extractor
- **Data Validation** - Gene, Allele, Disease, Chemical, GO Term, GO Annotations, Ortholog, and Ontology Term Resolver agents
- **Output** - Chat Output, CSV Formatter, TSV Formatter, JSON Formatter agents
- **My Custom Agents** - Custom agents you have created in Agent Workshop
- **Shared Agents** - Custom agents shared by other users in your project

The Agent Browser also includes filter tabs (All, Shared, Templates) at the top of the agent list to help narrow down agents quickly.

For each agent, you can view:
- **Base Prompt** - The core instructions given to the agent
- **Group-Specific Rules** - How the prompt is customized for each curator group (WormBase, FlyBase, MGI, ZFIN, RGD, SGD, Xenbase)
- **Combined View** - See the base prompt with group rules injected
- **Tools** - The tools available to each agent (listed in the agent card)
- **Domain Envelope Metadata** - For extraction agents, the curatable objects,
  field paths, schema/provider references, source-of-truth notes, and automatic
  validation policy supplied by the domain pack

**Domain Envelope Metadata**

Extraction agents that use domain packs show what kind of envelope they produce.
This includes object types, fields, required fields, definition state, provider
refs, active validator bindings, under-development validator metadata, and
export-blocking policy. The important idea is simple: the domain envelope object
is the saved curation record. Review tables and export payloads are generated
from that saved object.

Active default validators run automatically after extraction. Under-development
validators are shown as roadmap or context metadata and are not scheduled.
Validation findings are separate from attachment metadata: findings are the
current results written back to the envelope, while export and submission
blockers describe whether the reviewed envelope is ready for final actions.

Extraction and validation have separate responsibilities. First-pass extraction
agents read uploaded papers, record evidence, and preserve paper-backed
proposals or selector hints. They can use the narrow species/provider/taxon
context helper when organism context is needed, but final gene, allele, disease,
chemical, phenotype, ontology, reference, relation, and data-provider resolution
belongs to validator agents. Validator results and materialization are what make
resolved fields authoritative.

**Clickable Tool Names**

Tool names in agent cards are clickable! Click any tool name to open a detailed panel showing:
- **Description** - What the tool does
- **Parameters** - Input parameters with types and descriptions
- **Methods** - For multi-method tools (like database queries), see all available methods with examples
- **Agent Context** - Which methods are relevant to the selected agent

This helps you understand exactly what capabilities each agent has and how they interact with databases and APIs. When comparing extractor and validator agents, check which document/evidence tools the extractor can use, which broad lookup tools are deliberately unavailable to it, and which validator tools perform authoritative database, API, or ontology resolution.

**Ask Opus about agents:**
- "Why does this agent look for negative evidence?"
- "I think this prompt is missing guidance about [organism-specific convention]"
- "Can you help me write a suggestion to improve this?"
- "What does this instruction mean in practice?"
- "Can Agent Studio custom agents inspect the repository source code?"
- "Is there a tool policy that prevents this tool from being attached?"
- "Which tools can this extractor use, which lookup tools are deliberately unavailable, and which validator materializes the final fields?"

### Flows Tab

Build visual curation workflows and chat with Opus about them. See **[Curation Flows](CURATION_FLOWS.md)** for the complete guide to building flows.

**Flow Builder (Left Panel)**

Create workflows by dragging agents onto a canvas and connecting them:
- 15 available agents from extraction to file output
- Save, load, and reuse flows
- Generate downloadable CSV, TSV, or JSON files
- Inspect which domain-envelope objects an extraction node will produce
- Inspect automatic validators attached from domain-pack metadata

**Verify with Claude (Important!)**

Before running a flow, click the **"Verify with Claude"** button. Claude will:
- Check your flow structure for issues
- Identify missing connections or problematic configurations
- Suggest improvements
- Confirm your flow is ready to run

This is especially valuable when building new flows or troubleshooting ones that aren't working as expected.

**Automatic Validation Attachments**

When an extraction agent declares domain-pack validation metadata, Flow Builder
attaches the default active validators to the extraction node. Active validators
are enabled by default. Curators can skip an active default validator only when
flow configuration replaces or supplements it with explicit validation for the
same field or object. Validators explicitly marked by the domain pack as not
allowing flow replacement stay locked on.
Under-development validators remain visible metadata only and do not run.

To add a custom validation step, place a data-validation agent after the
extractor and use its steering prompt to name the envelope object, field path, or
curation concern you want checked. Custom validation agents are saved as regular
flow nodes; automatic validation remains controlled by the extraction agent's
domain-pack metadata.

**Ask Opus about flows:**
- "Does this flow make sense for extracting expression data?"
- "What agent should I add to map anatomy terms to WBbt IDs?"
- "Why isn't my flow generating the output I expected?"
- "Which validators will run for this extraction agent, and what prompt does each validator agent use?"

When you ask Chat with Claude how validation works, Claude can inspect the
domain-pack validation plan. If an active binding includes a validator-agent ID,
Claude can use the existing Agent Studio prompt tools to inspect that validator
agent's prompt, tools, and group-specific rules.

### Agent Workshop Tab

Create and test custom versions of agent prompts without affecting the live system.

**What is a Custom Agent?**

A custom agent is your personal copy of a system agent's prompt. You can edit the instructions, add per-group overrides, and use it in flows without changing anything for other users. Custom agents you create also appear in the Flow Builder agent palette under "My Custom Agents".

If you clone a domain-pack extraction agent, the Workshop shows what the agent produces on one line under "What it produces", with a count of automatic checks and a **View envelope** link to the full envelope in the Agents tab. Editing the prompt does not edit the domain pack schema, field paths, validators, export policy, or submission policy. Those remain controlled by the installed package metadata.

**The Workshop Layout**

- **Header** - Shows the agent's icon, name, and where it came from ("Template: ...", "Cloned from ...", or "From scratch"), plus a status pill: Unsaved changes, Saving, Saved, or Save failed. The header holds the **Open**, **New**, and **Save** buttons and a **More** menu with **Save as**, **Manage agents**, and **Delete agent**.
- **Navigation** - Four sections: **Setup**, **Prompt**, **Tools**, and **Versions**. An orange dot marks a section with unsaved edits. Tools shows how many tools are attached and Versions shows how many versions exist. The **Help** group has **Ask Claude**, which opens a discussion of your draft in the right-panel chat.

**Starting a New Agent**

Click **New** (or open the Workshop for the first time) to see the start screen with three choices:

- **From a template** - Start from a package agent and adjust its prompt
- **From scratch** - Write the prompt yourself; built-in instructions still apply
- **Clone one of yours** - Copy an agent you already saved

Choosing one lands you on Setup with the origin selected. You can change the starting point later on Setup.

You can also get here quickly from the Agents tab: click "Clone to Workshop" on any agent's detail panel.

**Setup**

1. **Starting point** - Template, Scratch, or Clone, with the template or clone source picker beside it. A note under the picker explains any group restriction the template carries.
2. **Identity** - Pick an icon, name the agent, and add a short description.
3. **What it produces** - One line showing the envelope, the number of automatic checks, and the **View envelope** link.
4. **Model** - Choose the model and, when the model supports it, a reasoning level. Higher levels are slower but better for difficult tasks. Open **Model guidance** to read the description, recommendations, and what to avoid. Not sure which to pick? Click **Ask Claude which model fits**.
5. **Sharing** - **Visibility** sets who can see the agent (Private, or Shared with project). **Available to groups** restricts who can run it. If the template or clone source already limits groups, a locked note says you can narrow that list but not widen it.

**Prompt**

The layer strip at the top shows what your prompt builds on: **Built-in**, **Output structure**, **Template**, and **Your prompt**, each with its length. The first three are read-only and marked with a lock. Click one to read it in the editor pane. **Your prompt** is the layer you edit; it replaces the template prompt. **Reset to template** puts the template text back.

Under **Group-specific instructions**, click a group button to see or edit that group's instructions. A group with your own text shows an "edited" badge. **Reset to template** removes your override for that group. The **Add group instructions at runtime** switch controls whether group instructions are included when the agent runs. Click **Discuss prompt changes with Claude** to get feedback on the prompt.

**Tools**

Attached tools appear in a table with each tool's purpose and any policy note. Click the remove button on a row to detach a tool.

1. Click **Add tools** to open the tool library
2. Search or filter by category, then check the tools you want
3. Click **Attach N tools**

Tools listed as "Disabled by policy for custom agents" appear with the reason but cannot be selected.

If you need a tool that does not exist yet, click **New request**, describe it, and click **Send request**. Your requests to developers appear below with their status (New, Reviewed, In progress, Shipped, or Declined). Click **Ask Claude to draft a request** if you want help writing one.

**Versions**

Every save creates a new version. The Versions table lists each version with its note and date; the current version is marked. Click **Revert** on an older version to create a new version from it. Nothing is deleted.

**Saving**

- **Save** opens a small dialog that names the version it creates, lists which sections changed, and lets you add an optional note. The Save button is enabled only when there is something to save.
- **Save as** (in the More menu) saves a copy under a new name and leaves the original unchanged.
- If you click **New** or open another agent while you have unsaved edits, the Workshop asks whether to discard them or keep editing. Closing the browser tab also warns you.

**Icon Picker**

When creating or editing a custom agent, select an icon on Setup to help identify your agent in the palette and flow canvas.

**Using Custom Agents in Flows**

Custom agents appear in the Flow Builder palette under "My Custom Agents". You can drag them into flows just like system agents.

**Ask Claude**

Click **Ask Claude** in the Workshop navigation to send your current draft to Opus for review. When the Agent Workshop tab is active, the right-panel chat is aware of your workshop context: your selected template source, draft prompt, and group settings. You can ask Claude to:
- "Critique this draft and suggest concrete edits"
- "Help me restructure this prompt for clarity"
- "What would happen if I changed this instruction?"
- "Does this prompt still produce the required domain-envelope fields?"
- "Which automatic validators will run for this agent?"
- "Does this custom extractor still keep proposed fields separate from validator-materialized fields?"

## Discussing a Chat Response

If you want to talk about the results from a conversation you're having in the main chat, you can bring that into Agent Studio:

1. In the main chat, find the AI response you want to discuss
2. Click the **triple-dot menu (⋮)** on that message
3. Select **"Open in Agent Studio"**

This opens Agent Studio with your conversation loaded, so Opus knows exactly what you're referring to. You can then ask questions like:
- "Why did the AI suggest this ontology term instead of that one?"
- "The AI missed the gene mentioned in paragraph 3 - what went wrong?"
- "Can you help me understand why I got this response?"
- "Which envelope object and field path did this validation finding target?"
- "Was this lookup attempt a final failure or just part of the audit trail?"

When a trace is available, Claude can inspect the TraceReview summary,
extraction diagnostic report, ordered model/tool/event reconstruction, exact
prompt and tool payload chunks, validation timeline, token/cost accounting, and
duplicate-context reports. This lets Claude explain what the AI actually did
before suggesting whether the issue is missing routing, missing data, or prompt
behavior.

This is the best way to get help understanding unexpected AI behavior or to formulate improvement suggestions.

## Understanding Validation Findings

Validation findings are attached to envelope objects or fields. A finding may be
informational, a warning, an error, or a blocker. Findings also have a status,
such as open, resolved, or waived. Export and submission previews block on open
error/blocker findings unless metadata allows a curator override or waiver.

Database-backed lookup tools may include `lookup_attempts`. Treat those attempts
as an audit trail. They show what was tried, which provider was used, how many
matches were found, and whether an individual attempt was successful,
ambiguous, not found, transient, blocked, or under development. A transient
attempt can still appear in the audit trail even when a later retry produced a
successful top-level lookup result.

For domain-envelope runs, a complete-looking extraction event is not enough to
prove validation succeeded. Check the validation findings and lookup attempts to
see which validator binding ran, what `DomainValidationRequest` fields it
received, what lookup it performed, and whether the final result was resolved,
unresolved, ambiguous, unavailable, or only under-development metadata.

Curator review is driven by validation findings and field paths. A review action
may update a bounded field, resolve or waive a finding when policy allows it, or
leave the finding open for package or data follow-up. The envelope history keeps
that trail.

## Submitting Feedback and Suggestions

Your domain expertise is invaluable for improving the AI. Agent Studio provides two ways to submit suggestions:

### AI-Assisted Suggestions

Click **Send feedback** (the light bulb) in the chat header, then choose **AI-assisted**. Opus will:
- Review your conversation
- Summarize the issue you've discussed
- Draft a concrete improvement suggestion
- Submit it for your confirmation

**When to use:** After discussing a specific issue with Opus, this is the fastest way to submit actionable feedback.

### Manual Suggestions

Click **Send feedback** in the chat header, then choose **Manual** to fill out a suggestion form yourself:

- **Suggestion Type:**
  - *Improvement* - General enhancement
  - *Bug* - Incorrect or unexpected behavior
  - *Clarification* - Ambiguous instructions
  - *Group-Specific* - Change needed for your group
  - *Missing Case* - Scenario the prompt doesn't handle
  - *General* - Feedback not tied to a specific prompt
- **Summary** - Brief description (1-2 sentences)
- **Detailed Reasoning** - Why this change is needed
- **Proposed Change** (optional) - Specific wording you'd suggest

**When to use:** When you have a clear suggestion and don't need Opus's help drafting it.

## Tips for Effective Feedback

### Be Specific
Instead of "The AI is wrong," try "When I asked about gene X, the AI said Y, but according to [source], it should be Z."

### Include Examples
If you see a pattern of errors, describe 2-3 specific cases. This helps identify whether it's a prompt issue or something else.

### Share Your Group's Conventions
If your group has specific naming conventions, annotation rules, or curation practices that the AI doesn't follow, explain them. **You're the expert on your organism!**

### Check Group-Specific Rules First
Before suggesting a change to a base prompt, check if your group already has specific rules in the Agent Browser. The issue might be that your group's rules need updating rather than the base prompt.

### Use "Open in Agent Studio"
When providing feedback about a specific interaction, always use the triple-dot menu to open it in Agent Studio rather than describing it manually. This gives Opus (and the development team) the full context.

## What Happens to Your Suggestions

When you submit a suggestion:

1. It's sent to the development team with full context
2. The team reviews it for feasibility and impact
3. If approved, the prompt is updated in the next deployment
4. Complex suggestions may require discussion before implementation

Your suggestions help make the AI better for everyone!

## Common Questions

### Do I need to select an agent to submit feedback?

No. If you have feedback based on a conversation or general observation, you can submit "General" feedback without selecting a specific agent.

### Can I see what prompts are currently being used?

Yes! That's the main purpose of the Agents tab. Browse all agent prompts and see exactly what instructions each agent receives.

### Why are there group-specific rules?

Each curator group has organism-specific conventions and curation practices. Group rules customize the AI to respect these conventions, for example using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### How do prompts layer together? Can they conflict?

Each agent has a base prompt, optional group-specific rules, and optional flow custom instructions. These combine in a defined priority order: flow custom instructions (highest) > base prompt > group rules. Flow instructions override everything else for that step. See **[How Prompts Layer Together](CURATION_FLOWS.md#how-prompts-layer-together)** in the Curation Flows guide for full details.

### What's the difference between Agent Studio's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks - it routes your questions to specialists who query databases. Agent Studio's Opus is for discussing how the AI works, understanding specific responses, and improving the system.

### How do I build curation flows?

See the **[Curation Flows](CURATION_FLOWS.md)** guide for complete documentation.

## Need Help?

If you have questions about using Agent Studio or need help formulating feedback, just ask Opus! It's designed to help you translate your domain expertise into actionable suggestions.
