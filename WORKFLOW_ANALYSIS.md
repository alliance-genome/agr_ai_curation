# Workflow Analysis Guide

The Workflow Analysis tool helps curators understand, analyze, and improve the AI prompts that power the curation assistant. It provides transparency into how the AI agents work, allows you to review interactions in detail, and gives you a direct way to suggest improvements.

## Accessing Workflow Analysis

Click **"Workflow Analysis"** in the navigation bar at the top of the application.

## What You'll Find

### Left Panel: Chat with Claude Opus 4.5

A dedicated chat interface where you can have conversations with Claude Opus 4.5 (Anthropic's most capable model) about prompts and AI behavior.

**Key Features:**
- **Extended Thinking** - Toggle deep reasoning mode for complex analyses (appears as 💭 icon in Opus responses)
- **Effort Control** - Adjust thinking depth from "Low" to "Maximum" using the slider
- **Conversation History** - All chats are preserved and can be continued later
- **Tool Access** - Opus can submit suggestions directly to the development team

**Example questions you can ask:**
- "Why does the gene expression agent extract information this way?"
- "How could we improve this prompt for FlyBase conventions?"
- "What would happen if we changed this instruction?"
- "Can you explain what this section of the prompt does?"
- "Review this trace and identify any prompt issues"

### Right Panel: Prompt Browser

Browse all agent prompts organized by category:

- **Routing** - Supervisor agent that routes queries to specialists
- **Extraction** - Gene Expression Specialist and Formatter agents
- **Database Query** - Gene, Allele, Disease, Chemical, GO Term, and other lookup agents
- **Validation** - Ontology mapping and term validation agents

For each agent, you can view:
- **Base Prompt** - The core instructions given to the agent
- **MOD-Specific Rules** - How the prompt is customized for each Model Organism Database (WormBase, FlyBase, MGI, ZFIN, RGD, SGD)
- **Combined View** - See the base prompt with MOD rules injected

## Submitting Feedback and Suggestions

Workflow Analysis provides multiple ways to suggest improvements:

### 1. AI-Assisted Suggestions

Click the **"AI-Assisted"** (✨) button in the chat header. This asks Opus to help you draft a suggestion based on your conversation. Opus will:
- Analyze the full conversation history
- Summarize the issue you've discussed
- Propose a concrete improvement
- Submit it for your confirmation

**When to use:** After discussing a specific issue or improvement with Opus, this is the fastest way to submit actionable feedback.

### 2. Manual Suggestions

Click the **"Manual"** (✍️) button to fill out a suggestion form yourself. You'll provide:
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
3. Select **"Open in Workflow Analysis"**

This opens Workflow Analysis with the full trace context automatically loaded, including:
- Your original query
- The AI's response
- Which agents were involved
- What tool calls were made
- Routing decisions
- Performance metrics

You can then discuss this specific interaction with Opus and submit targeted suggestions.

## Understanding Trace Context

When you open Workflow Analysis from a trace, Opus has access to:

- **Trace ID** - Unique identifier for the interaction (clickable fingerprint icon 🔍)
- **User Query** - What you asked
- **Final Response** - What the AI answered
- **Agents Involved** - Which specialists handled the query
- **Tool Calls** - What database queries or operations were performed (viewable via trace review service)
- **Timing Data** - How long each operation took

### Viewing Detailed Trace Information

Click the **fingerprint icon (🔍)** next to any message to:
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

## Extended Thinking Mode

The 💭 icon in Opus responses indicates Extended Thinking mode is active. When enabled, Opus performs additional reasoning steps before responding, which:

- **Improves accuracy** for complex analyses
- **Shows its reasoning** in a collapsed section you can expand
- **Takes more time** (typically 10-30 seconds longer)

**When to use Extended Thinking:**
- Analyzing complex prompt behavior
- Reviewing traces with multiple agent interactions
- Comparing different approaches to a problem
- Debugging subtle issues

**When to disable it:**
- Quick questions with straightforward answers
- Iterative conversations where speed matters
- Brainstorming sessions

Use the **effort slider** (Low → Medium → High → Maximum) to control how deeply Opus thinks.

## Common Questions

### Do I need to select an agent to submit feedback?

No. If you have feedback based on a trace or general conversation that isn't about a specific agent's prompt, you can submit "General" feedback without selecting an agent.

### Can I see what prompts are currently being used?

Yes! That's the main purpose of Workflow Analysis. Browse the right panel to see the exact instructions given to each agent.

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI's behavior to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### What's the difference between Workflow Analysis's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks. Workflow Analysis's chat uses Claude Opus 4.5 directly, which is better for open-ended discussions about prompts and AI behavior. Opus in Workflow Analysis also has access to:
- Extended Thinking mode for deeper reasoning
- The suggestion submission tool
- Full trace context when opened from the main chat
- Conversation history preservation

### How do I use the trace review feature?

1. Click the fingerprint icon (🔍) next to any message in the main chat or Workflow Analysis
2. This copies the trace ID and shows a detailed breakdown
3. You can discuss the trace with Opus, who can analyze:
   - Tool call patterns (e.g., redundant searches)
   - Routing decisions
   - Performance issues
   - Citation accuracy

### What's the difference between AI-Assisted and Manual feedback?

- **AI-Assisted (✨)**: Opus reviews your conversation and drafts the suggestion for you. Best when you've been discussing an issue and want Opus to formalize it.
- **Manual (✍️)**: You fill out the form yourself. Best when you have a clear, specific suggestion ready to submit.

Both options capture the same context (trace ID, conversation, agent/MOD selection) in the final submission.

## Need Help?

If you have questions about using Workflow Analysis or need help formulating feedback, just ask Opus! It's designed to help you navigate prompt engineering concepts and translate your domain expertise into actionable suggestions.

**Pro tip:** Start a conversation with Opus about any aspect of the system you're curious about. It has access to all the prompts and can explain how they work, why certain decisions were made, and how to improve them.
