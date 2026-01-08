# Agent Studio Guide

Agent Studio helps you understand how the AI curation agents work and gives you tools to improve them. You can chat with Claude Opus 4.5 about why the AI made certain decisions, browse the instructions given to each agent, build visual curation workflows, and submit suggestions based on your domain expertise.

## Accessing Agent Studio

Click **"Agent Studio"** in the navigation bar at the top of the application.

## What You'll Find

Agent Studio has four main tabs:

### 1. Opus Chat

A chat interface where you can talk directly with Claude Opus 4.5 (Anthropic's most capable model) about the AI's behavior and your curation questions.

**What you can ask Opus:**
- "Why did the AI suggest this ontology term instead of that one?"
- "Can you explain what the gene expression agent is looking for?"
- "I think this prompt is missing something - can you help me write a suggestion?"
- "Look at this trace and tell me why the AI missed the gene in paragraph 3"
- "What databases does the disease agent query?"

**Key Features:**
- **Understands the system** - Opus knows how all the agents work and can explain their behavior
- **Trace Analysis** - Open traces from the main chat to discuss specific interactions
- **Direct Feedback** - Submit suggestions via AI-Assisted or Manual buttons

### 2. Prompt Browser

Browse the instructions given to each AI agent, organized by category:

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

This is helpful when you want to understand *why* an agent behaves a certain way, or when you want to suggest improvements.

### 3. Flow Builder

Create visual curation workflows that chain multiple agents together. See **[Curation Flows](CURATION_FLOWS.md)** for the complete guide.

**Quick Overview:**
- Drag-and-drop interface for building workflows
- 12+ available agents from extraction to file output
- Save, load, and share flows with other curators
- Run flows with "Verify with Claude" integration

### 4. Trace Analysis

When you open a trace from the main chat (via the fingerprint icon or triple-dot menu), you can see exactly what happened during that interaction:

- **Your Query** - What you asked
- **AI's Response** - What it answered
- **Agents Involved** - Which specialists handled your question
- **Database Queries** - What searches were performed
- **Timing** - How long each step took

This helps you understand why the AI gave a particular answer and identify where things went wrong if the response wasn't what you expected.

## Submitting Feedback and Suggestions

Your domain expertise is invaluable for improving the AI. Agent Studio provides multiple ways to suggest improvements:

### 1. AI-Assisted Suggestions

Click the **"AI-Assisted"** button in the chat header. Opus will:
- Review your conversation
- Summarize the issue you've discussed
- Draft a concrete improvement suggestion
- Submit it for your confirmation

**When to use:** After discussing a specific issue with Opus, this is the fastest way to submit actionable feedback.

### 2. Manual Suggestions

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

### 3. Trace-Based Feedback (from Main Chat)

This is the most powerful way to provide feedback because it includes full context:

1. In the main chat, find an AI response you want to discuss
2. Click the **triple-dot menu (⋮)** on that message
3. Select **"Open in Agent Studio"**

This opens Agent Studio with the full trace context automatically loaded. You can then discuss this specific interaction with Opus and submit targeted suggestions.

## Tips for Effective Feedback

### Be Specific
Instead of "The AI is wrong," try "When I asked about gene X, the AI said Y, but according to [source], it should be Z."

### Include Examples
If you see a pattern of errors, describe 2-3 specific cases. This helps identify whether it's a prompt issue or something else.

### Share Your MOD's Conventions
If your MOD has specific naming conventions, annotation rules, or curation practices that the AI doesn't follow, explain them. **You're the expert on your organism!**

### Check MOD-Specific Rules First
Before suggesting a change to a base prompt, check if your MOD already has specific rules in the Prompt Browser. The issue might be that your MOD's rules need updating rather than the base prompt.

### Use Trace Context
When providing feedback about a specific interaction, always open it from the trace (triple-dot menu) rather than describing it manually. This ensures the development team has the full context.

## What Happens to Your Suggestions

When you submit a suggestion:

1. It's sent to the development team with full context
2. The team reviews it for feasibility and impact
3. If approved, the prompt is updated in the next deployment
4. Complex suggestions may require discussion before implementation

Your suggestions help make the AI better for everyone!

## Common Questions

### Do I need to select an agent to submit feedback?

No. If you have feedback based on a trace or general conversation, you can submit "General" feedback without selecting a specific agent.

### Can I see what prompts are currently being used?

Yes! That's the main purpose of the Prompt Browser tab. Browse all agent prompts and see exactly what instructions each agent receives.

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### What's the difference between Agent Studio's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks - it routes your questions to specialists who query databases. Agent Studio's Opus Chat is for open-ended discussions about how the AI works, why it made certain decisions, and how to improve it.

### How do I build curation flows?

See the **[Curation Flows](CURATION_FLOWS.md)** guide for complete documentation.

## Need Help?

If you have questions about using Agent Studio or need help formulating feedback, just ask Opus! It's designed to help you translate your domain expertise into actionable suggestions.

**Pro tip:** If you're curious about why the AI does something a certain way, start by asking Opus. It can explain how the agents work and help you decide whether to submit a suggestion.
