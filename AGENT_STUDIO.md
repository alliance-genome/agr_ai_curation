# Agent Studio Guide

Agent Studio helps you understand how the AI curation agents work and gives you tools to improve them. You can browse agent prompts, build visual curation workflows, and chat with Claude Opus 4.5 about any of it.

## Accessing Agent Studio

Click **"Agent Studio"** in the navigation bar at the top of the application.

## What You'll Find

Agent Studio has two main tabs: **Prompts** and **Flows**. Both tabs include a chat panel where you can talk with Claude Opus 4.5.

### Opus Chat (Left Panel)

On both tabs, the left panel is your chat with Claude Opus 4.5. You can ask Opus about whatever you're viewing on the right - prompts or flows.

### Prompts Tab

Browse the instructions given to each AI agent and chat with Opus about them.

**Prompt Browser (Right Panel)**

See all agent prompts organized by category:
- **Routing** - Supervisor agent that routes your queries to specialists
- **Extraction** - Gene Expression Specialist and Formatter agents
- **Database Query** - Gene, Allele, Disease, Chemical, GO Term, and other lookup agents
- **Validation** - Ontology mapping and term validation agents
- **Output** - Chat Output, CSV Formatter, TSV Formatter, JSON Formatter agents

For each agent, you can view:
- **Base Prompt** - The core instructions given to the agent
- **MOD-Specific Rules** - How the prompt is customized for each Model Organism Database (WormBase, FlyBase, MGI, ZFIN, RGD, SGD, Xenbase)
- **Combined View** - See the base prompt with MOD rules injected
- **Version History** - Track changes to prompts over time

**Ask Opus about prompts:**
- "Why does this agent look for negative evidence?"
- "I think this prompt is missing guidance about [organism-specific convention]"
- "Can you help me write a suggestion to improve this?"
- "What does this instruction mean in practice?"

### Flows Tab

Build visual curation workflows and chat with Opus about them. See **[Curation Flows](CURATION_FLOWS.md)** for the complete guide to building flows.

**Flow Builder (Right Panel)**

Create workflows by dragging agents onto a canvas and connecting them:
- 12+ available agents from extraction to file output
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
Before suggesting a change to a base prompt, check if your MOD already has specific rules in the Prompt Browser. The issue might be that your MOD's rules need updating rather than the base prompt.

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

Yes! That's the main purpose of the Prompts tab. Browse all agent prompts and see exactly what instructions each agent receives.

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### What's the difference between Agent Studio's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks - it routes your questions to specialists who query databases. Agent Studio's Opus is for discussing how the AI works, understanding specific responses, and improving the system.

### How do I build curation flows?

See the **[Curation Flows](CURATION_FLOWS.md)** guide for complete documentation.

## Need Help?

If you have questions about using Agent Studio or need help formulating feedback, just ask Opus! It's designed to help you translate your domain expertise into actionable suggestions.
