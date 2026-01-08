# Agent Studio Guide

Agent Studio is a powerful development environment for understanding, analyzing, and improving the AI agents that power the curation system. It provides transparency into how agents work, allows you to review interactions in detail, chat with Claude Opus 4.5 about prompts and traces, and build visual curation workflows.

## Accessing Agent Studio

Click **"Agent Studio"** in the navigation bar at the top of the application.

## What You'll Find

Agent Studio has four main components accessible via tabs:

### 1. Opus Chat

A dedicated chat interface where you can have conversations with Claude Opus 4.5 (Anthropic's most capable model) about prompts, traces, and AI behavior.

**Key Features:**
- **Conversation History** - All chats are preserved and can be continued later
- **Tool Access** - Opus has powerful diagnostic capabilities (see below)
- **Trace Analysis** - Open traces from the main chat to discuss specific interactions
- **Direct Feedback** - Submit suggestions via AI-Assisted or Manual buttons

**What Opus Can Do:**

Opus has access to the same tools that specialist agents use, making it a true debugging partner:

- **Analyze Traces** - Query trace data dynamically (tool calls, conversation flow, citations, token usage, agent context)
- **Read Logs** - Access Docker container logs to investigate errors
- **Query Databases** - Search genes, alleles, ontology terms via AGR Curation Database
- **Execute SQL** - Run diagnostic queries on the curation database
- **Call APIs** - Query ChEBI (chemicals), QuickGO (GO terms), GO annotations
- **Inspect Prompts** - View any agent's prompt, including MOD-specific rules
- **Submit Feedback** - Create suggestions for the development team

**Example questions you can ask:**
- "Can you check the trace for this interaction and see why it failed?"
- "Look at the backend logs and tell me what went wrong"
- "Query the database for this gene symbol and verify the ID is correct"
- "What does the gene expression agent prompt say about anatomy terms?"
- "Review this trace - are there any duplicate tool calls or inefficiencies?"

### 2. Prompt Browser

Browse all agent prompts organized by category:

- **Routing** - Supervisor agent that routes queries to specialists
- **Extraction** - Gene Expression Specialist and Formatter agents
- **Database Query** - Gene, Allele, Disease, Chemical, GO Term, and other lookup agents
- **Validation** - Ontology mapping and term validation agents
- **Output** - Chat Output, CSV Formatter, TSV Formatter, JSON Formatter agents

For each agent, you can view:
- **Base Prompt** - The core instructions given to the agent
- **MOD-Specific Rules** - How the prompt is customized for each Model Organism Database (WormBase, FlyBase, MGI, ZFIN, RGD, SGD, Xenbase)
- **Combined View** - See the base prompt with MOD rules injected
- **Version History** - Track changes to prompts over time

### 3. Flow Builder

Create visual curation workflows that chain multiple agents together. See **[Curation Flows](CURATION_FLOWS.md)** for comprehensive documentation on building and running flows.

**Quick Overview:**
- Drag-and-drop interface for building workflows
- 12+ available agents from extraction to file output
- Save, load, and share flows with other curators
- Run flows with "Verify with Claude" integration

### 4. Trace Analysis

When you open a trace from the main chat (via the fingerprint icon or triple-dot menu), Agent Studio provides detailed analysis:

- **Trace ID** - Unique identifier for the interaction
- **User Query** - What you asked
- **Final Response** - What the AI answered
- **Agents Involved** - Which specialists handled the query
- **Tool Calls** - What database queries or operations were performed
- **Timing Data** - How long each operation took
- **PDF Citations** - Document sections referenced

## Submitting Feedback and Suggestions

Agent Studio provides multiple ways to suggest improvements:

### 1. AI-Assisted Suggestions

Click the **"AI-Assisted"** button in the chat header. This asks Opus to help you draft a suggestion based on your conversation. Opus will:
- Analyze the full conversation history
- Summarize the issue you've discussed
- Propose a concrete improvement
- Submit it for your confirmation

**When to use:** After discussing a specific issue or improvement with Opus, this is the fastest way to submit actionable feedback.

### 2. Manual Suggestions

Click the **"Manual"** button to fill out a suggestion form yourself. You'll provide:
- **Suggestion Type** - What kind of change is this?
  - *Improvement* - General enhancement
  - *Bug* - Incorrect or unexpected behavior
  - *Clarification* - Ambiguous instructions
  - *MOD-Specific* - Change needed for a specific MOD
  - *Missing Case* - Scenario the prompt doesn't handle
  - *General* - Feedback not tied to a specific prompt
- **Summary** - Brief description (1-2 sentences)
- **Detailed Reasoning** - Why this change is needed
- **Proposed Change** (optional) - Specific wording you'd suggest

**When to use:** When you have a well-formed suggestion and don't need Opus's help drafting it.

### 3. Trace-Based Feedback (from Main Chat)

This is the most powerful way to provide feedback because it includes full context:

1. In the main chat interface, find an AI response you want to discuss
2. Click the **triple-dot menu (⋮)** on that message
3. Select **"Open in Agent Studio"**

This opens Agent Studio with the full trace context automatically loaded, including:
- Your original query
- The AI's response
- Which agents were involved
- What tool calls were made
- Routing decisions
- Performance metrics

You can then discuss this specific interaction with Opus and submit targeted suggestions.

## Understanding Trace Context

When you open Agent Studio from a trace, Opus has access to:

- **Trace ID** - Unique identifier for the interaction (clickable fingerprint icon)
- **User Query** - What you asked
- **Final Response** - What the AI answered
- **Agents Involved** - Which specialists handled the query
- **Tool Calls** - What database queries or operations were performed
- **Timing Data** - How long each operation took

### Viewing Detailed Trace Information

Click the **fingerprint icon** next to any message to:
- Copy the trace ID
- View detailed trace breakdown showing:
  - All tool calls in chronological order
  - Supervisor routing decisions
  - PDF citations and section references
  - Performance metrics
  - Full conversation flow

## Tips for Effective Feedback

### Be Specific
Instead of "The AI is wrong," try "When I asked about gene X, the AI said Y, but according to [source], it should be Z."

### Include Examples
If you see a pattern of errors, describe 2-3 specific cases. This helps identify whether it's a prompt issue or something else.

### Explain Your MOD's Conventions
If your MOD has specific naming conventions, annotation rules, or curation practices that the AI doesn't follow, explain them. You're the expert on your organism!

### Distinguish Types of Issues
- **Prompt issue** - The AI is following its instructions but the instructions are wrong
- **Model limitation** - The AI can't reliably do what's being asked (e.g., complex math)
- **Ambiguous source** - The paper itself is unclear, not the AI's interpretation

### Check MOD-Specific Rules First
Before suggesting a change to a base prompt, check if your MOD already has specific rules. The issue might be that your MOD's rules need updating rather than the base prompt.

### Use Trace Context
When providing feedback about a specific interaction, always open it from the trace (triple-dot menu) rather than describing it manually. This ensures developers have the full context.

## What Happens to Your Suggestions

When you submit a suggestion:

1. It's sent to the development team via email notification with full context
2. If submitted from a trace, the email includes:
   - The trace ID for debugging
   - The conversation context
   - Selected agent and MOD (if applicable)
3. The team reviews it for technical feasibility and impact
4. If approved, the prompt is updated in the next deployment
5. Complex suggestions may require discussion before implementation

Your suggestions help make the AI better for everyone. Even if a specific change isn't implemented, the feedback helps us understand curator needs.

## Common Questions

### Do I need to select an agent to submit feedback?

No. If you have feedback based on a trace or general conversation that isn't about a specific agent's prompt, you can submit "General" feedback without selecting an agent.

### Can I see what prompts are currently being used?

Yes! That's the main purpose of the Prompt Browser tab. Browse all agent prompts organized by category and see exactly what instructions each agent receives.

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI's behavior to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### What's the difference between Agent Studio's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks. Agent Studio's Opus Chat uses Claude Opus 4.5 directly, which is better for open-ended discussions about prompts and AI behavior. Opus in Agent Studio also has access to:
- Powerful diagnostic tools (trace analysis, logs, database queries, API calls)
- The ability to inspect prompts and system behavior
- The suggestion submission tool
- Full trace context when opened from the main chat
- Conversation history preservation

### How do I use the trace review feature?

1. Click the fingerprint icon next to any message in the main chat or Agent Studio
2. This copies the trace ID and shows a detailed breakdown
3. You can discuss the trace with Opus, who can analyze:
   - Tool call patterns (e.g., redundant searches)
   - Routing decisions
   - Performance issues
   - Citation accuracy

### What's the difference between AI-Assisted and Manual feedback?

- **AI-Assisted**: Opus reviews your conversation and drafts the suggestion for you. Best when you've been discussing an issue and want Opus to formalize it.
- **Manual**: You fill out the form yourself. Best when you have a clear, specific suggestion ready to submit.

Both options capture the same context (trace ID, conversation, agent/MOD selection) in the final submission.

### How do I build curation flows?

See the **[Curation Flows](CURATION_FLOWS.md)** guide for complete documentation on using the Flow Builder to create visual curation workflows.

## Need Help?

If you have questions about using Agent Studio or need help formulating feedback, just ask Opus! It's designed to help you navigate prompt engineering concepts and translate your domain expertise into actionable suggestions.

**Pro tip:** Start a conversation with Opus about any aspect of the system you're curious about. It has access to all the prompts and can explain how they work, why certain decisions were made, and how to improve them.
