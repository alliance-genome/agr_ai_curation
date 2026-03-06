# Agent Studio Guide

Agent Studio helps you understand how the AI curation agents work and gives you tools to improve them. You can browse agent prompts, build visual curation workflows, and chat with Claude Opus about any of it.

## Accessing Agent Studio

Click **"Agent Studio"** in the navigation bar at the top of the application.

## What You'll Find

Agent Studio has three main tabs: **Agents**, **Flows**, and **Agent Workshop**. All tabs include a chat panel where you can talk with Claude Opus.

### Opus Chat (Left Panel)

On both tabs, the left panel is your chat with Claude Opus. You can ask Opus about whatever you're viewing on the right - agent prompts or flows.

### Agents Tab

Browse the instructions given to each AI agent and chat with Opus about them.

**Agent Browser (Right Panel)**

See all agent prompts organized by subcategory:
- **System** - Supervisor Agent that routes your queries to specialists (internal, not available in Flow Builder)
- **PDF Extraction** - PDF Extraction Agent and Gene Expression Extractor
- **Data Validation** - Gene, Allele, Disease, Chemical, GO Term, GO Annotations, Ortholog, and Ontology Mapping agents
- **Output** - Chat Output, CSV Formatter, TSV Formatter, JSON Formatter agents
- **My Custom Agents** - Custom agents you have created in Agent Workshop
- **Shared Agents** - Custom agents shared by other users in your project

The Agent Browser also includes filter tabs (All, Shared, Templates) at the top of the agent list to help narrow down agents quickly.

For each agent, you can view:
- **Base Prompt** - The core instructions given to the agent
- **MOD-Specific Rules** - How the prompt is customized for each Model Organism Database (WormBase, FlyBase, MGI, ZFIN, RGD, SGD, Xenbase)
- **Combined View** - See the base prompt with MOD rules injected
- **Tools** - The tools available to each agent (listed in the agent card)

**Clickable Tool Names**

Tool names in agent cards are clickable! Click any tool name to open a detailed panel showing:
- **Description** - What the tool does
- **Parameters** - Input parameters with types and descriptions
- **Methods** - For multi-method tools (like database queries), see all available methods with examples
- **Agent Context** - Which methods are relevant to the selected agent

This helps you understand exactly what capabilities each agent has and how they interact with databases and APIs.

**Ask Opus about agents:**
- "Why does this agent look for negative evidence?"
- "I think this prompt is missing guidance about [organism-specific convention]"
- "Can you help me write a suggestion to improve this?"
- "What does this instruction mean in practice?"

### Flows Tab

Build visual curation workflows and chat with Opus about them. See **[Curation Flows](CURATION_FLOWS.md)** for the complete guide to building flows.

**Flow Builder (Right Panel)**

Create workflows by dragging agents onto a canvas and connecting them:
- 15 available agents from extraction to file output
- Save, load, and reuse flows
- Generate downloadable CSV, TSV, or JSON files

**Verify with Claude (Important!)**

Before running a flow, click the **"Verify with Claude"** button. Claude will:
- Check your flow structure for issues
- Identify missing connections or problematic configurations
- Suggest improvements
- Confirm your flow is ready to run

This is especially valuable when building new flows or troubleshooting ones that aren't working as expected.

**Ask Opus about flows:**
- "Does this flow make sense for extracting expression data?"
- "What agent should I add to map anatomy terms to WBbt IDs?"
- "Why isn't my flow generating the output I expected?"

### Agent Workshop Tab

Create and test custom versions of agent prompts without affecting the live system.

**What is a Custom Agent?**

A custom agent is your personal copy of a system agent's prompt. You can edit the instructions, add per-MOD overrides, and use it in flows — all without changing anything for other users. Custom agents you create also appear in the Flow Builder agent palette under "My Custom Agents".

**Getting Started**

1. **Choose a Getting Started mode** - Select **Template**, **Scratch**, or **Clone**
2. **Set your base configuration** - Pick template/clone source (if applicable), model, and tools
3. **Edit the prompt** - Modify instructions and optional per-MOD overrides
4. **Choose an icon** - Pick an emoji icon from the icon picker to identify your agent
5. **Save** - Use **File → Save Agent**. Each save creates a version you can revert to later.

You can also get here quickly from the Agents tab: click "Clone to Workshop" on any agent's detail panel.

**File Menu**

The Workshop toolbar provides these operations:

- **New Agent** - Start a new custom agent draft
- **Open Agent...** - Search and open a previously saved custom agent
- **Manage Agents...** - Open or delete saved custom agents
- **Save Agent** / **Save New Agent** - Save your current work (creates a new version)
- **Delete Agent** - Remove the current custom agent

**Icon Picker**

When creating or editing a custom agent, select an emoji icon to help identify your agent in the palette and flow canvas. Available icons include 🔧, 🧬, 📄, 🔍, 🧪, 📊, 🧠, ⚙️, ✨, 📝, 📚, 🧩, and more.

**Per-MOD Prompt Overrides**

Customize how your agent behaves for different Model Organism Databases:

1. Check **"Include MOD rules at runtime"** to apply MOD-specific rules when your custom agent runs
2. **Select a MOD** from the dropdown (e.g., WormBase, FlyBase, MGI)
3. **Edit the override** - Write MOD-specific instructions in the text area
4. **Reset to Template** - Revert a MOD override back to the template version

The Workshop shows which MODs have custom overrides and a total override count.

**Version History**

Every save creates a new version. You can add optional save notes to describe your changes. Revert to any previous version if an edit doesn't work out.

**Template Availability**

If a template source is unavailable for an older custom agent, Agent Workshop will show a warning that the custom agent cannot be executed until a valid template is selected.

**Tool Library**

Attach tools to your custom agent from the available tool library:

1. In the **Advanced Settings** section, expand the **Tools** accordion
2. Click **"Manage Tools"** to open the Tool Library dialog
3. Browse or search tools by name or category (Database, API, Document, Output)
4. Check the tools you want to attach and click **"Done"**

Some tools may be marked as "Not attachable by policy" -- these are reserved for specific system agents.

**Tool Requests**

If you need a new tool that does not exist yet:

1. Click **"Need a new tool? Ask Claude"** to draft a request with Claude's help
2. When ready, click **"Send to Developers"** to submit a formal tool request
3. Track the status of your requests in the **Tool Requests** accordion (pending, reviewed, in progress, completed, or declined)

**Visibility**

Control who can see your custom agent:

- **Private** - Only you can see and use this agent (default)
- **Shared with Project** - Other users in your project can see and use this agent in their flows

**Model Selection**

Choose the AI model that powers your custom agent:

1. Select a model from the **Model** dropdown (the Workshop shows model descriptions and recommendations)
2. If the model supports reasoning levels, choose a level (low, medium, high) -- higher levels are slower but better for difficult tasks
3. Not sure which model to pick? Click **"Confused about models? Chat with Claude"** for guidance

**Using Custom Agents in Flows**

Custom agents appear in the Flow Builder palette under "My Custom Agents". You can drag them into flows just like system agents.

**Discuss with Claude**

Click the **"Discuss with Claude"** button in the Workshop toolbar to send your current draft prompt to Opus for review. When the Agent Workshop tab is active, the left-panel chat is aware of your workshop context — your selected template source, draft prompt, and MOD settings. You can ask Claude to:
- "Critique this draft and suggest concrete edits"
- "Help me restructure this prompt for clarity"
- "What would happen if I changed this instruction?"

## Discussing a Chat Response

If you want to talk about the results from a conversation you're having in the main chat, you can bring that into Agent Studio:

1. In the main chat, find the AI response you want to discuss
2. Click the **triple-dot menu (⋮)** on that message
3. Select **"Open in Agent Studio"**

This opens Agent Studio with your conversation loaded, so Opus knows exactly what you're referring to. You can then ask questions like:
- "Why did the AI suggest this ontology term instead of that one?"
- "The AI missed the gene mentioned in paragraph 3 - what went wrong?"
- "Can you help me understand why I got this response?"

This is the best way to get help understanding unexpected AI behavior or to formulate improvement suggestions.

## Submitting Feedback and Suggestions

Your domain expertise is invaluable for improving the AI. Agent Studio provides two ways to submit suggestions:

### AI-Assisted Suggestions

Click the **"AI-Assisted"** button in the chat header. Opus will:
- Review your conversation
- Summarize the issue you've discussed
- Draft a concrete improvement suggestion
- Submit it for your confirmation

**When to use:** After discussing a specific issue with Opus, this is the fastest way to submit actionable feedback.

### Manual Suggestions

Click the **"Manual"** button to fill out a suggestion form yourself:

- **Suggestion Type:**
  - *Improvement* - General enhancement
  - *Bug* - Incorrect or unexpected behavior
  - *Clarification* - Ambiguous instructions
  - *MOD-Specific* - Change needed for your MOD
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

### Share Your MOD's Conventions
If your MOD has specific naming conventions, annotation rules, or curation practices that the AI doesn't follow, explain them. **You're the expert on your organism!**

### Check MOD-Specific Rules First
Before suggesting a change to a base prompt, check if your MOD already has specific rules in the Agent Browser. The issue might be that your MOD's rules need updating rather than the base prompt.

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

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### How do prompts layer together? Can they conflict?

Each agent has a base prompt, optional MOD-specific rules, and optional flow custom instructions. These combine in a defined priority order: flow custom instructions (highest) > base prompt > MOD rules. Flow instructions override everything else for that step. See **[How Prompts Layer Together](CURATION_FLOWS.md#how-prompts-layer-together)** in the Curation Flows guide for full details.

### What's the difference between Agent Studio's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks - it routes your questions to specialists who query databases. Agent Studio's Opus is for discussing how the AI works, understanding specific responses, and improving the system.

### How do I build curation flows?

See the **[Curation Flows](CURATION_FLOWS.md)** guide for complete documentation.

## Need Help?

If you have questions about using Agent Studio or need help formulating feedback, just ask Opus! It's designed to help you translate your domain expertise into actionable suggestions.
