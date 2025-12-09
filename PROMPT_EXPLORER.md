# Prompt Explorer Guide

The Prompt Explorer is a tool for curators to understand, analyze, and improve the AI prompts that power the curation assistant. It provides transparency into how the AI agents work and gives you a direct way to suggest improvements.

## Accessing Prompt Explorer

Click **"Prompt Explorer"** in the navigation bar at the top of the application.

## What You'll Find

### Left Panel: Chat with Claude Opus 4.5

A dedicated chat interface where you can have conversations with Claude Opus 4.5 (Anthropic's most capable model) about prompts and AI behavior.

**Example questions you can ask:**
- "Why does the gene expression agent extract information this way?"
- "How could we improve this prompt for FlyBase conventions?"
- "What would happen if we changed this instruction?"
- "Can you explain what this section of the prompt does?"

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

Prompt Explorer provides multiple ways to suggest improvements:

### 1. AI-Assisted Suggestions

Click the **"AI-Assisted"** button in the chat header. This asks Opus to help you draft a suggestion based on your conversation. Opus will:
- Summarize the issue you've discussed
- Propose a concrete improvement
- Ask for your confirmation before submitting

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

### 3. Trace-Based Feedback (from Main Chat)

This is the most powerful way to provide feedback because it includes full context:

1. In the main chat interface, find an AI response you want to discuss
2. Click the **triple-dot menu (⋮)** on that message
3. Select **"Open in Prompt Explorer"**

This opens Prompt Explorer with the full trace context automatically loaded, including:
- Your original query
- The AI's response
- Which agents were involved
- What tool calls were made
- Routing decisions

You can then discuss this specific interaction with Opus and submit targeted suggestions.

## Understanding Trace Context

When you open Prompt Explorer from a trace, Opus has access to:

- **Trace ID** - Unique identifier for the interaction
- **User Query** - What you asked
- **Final Response** - What the AI answered
- **Agents Involved** - Which specialists handled the query
- **Tool Calls** - What database queries or operations were performed

This context helps Opus understand exactly what happened and why, making it easier to identify prompt improvements.

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

## What Happens to Your Suggestions

When you submit a suggestion:

1. It's sent to the development team via email notification
2. The team reviews it for technical feasibility and impact
3. If approved, the prompt is updated in the next deployment
4. Complex suggestions may require discussion before implementation

Your suggestions help make the AI better for everyone. Even if a specific change isn't implemented, the feedback helps us understand curator needs.

## Common Questions

### Do I need to select an agent to submit feedback?

No. If you have feedback based on a trace or general conversation that isn't about a specific agent's prompt, you can submit "General" feedback without selecting an agent.

### Can I see what prompts are currently being used?

Yes! That's the main purpose of Prompt Explorer. Browse the right panel to see the exact instructions given to each agent.

### Why are there MOD-specific rules?

Each Model Organism Database has decades of curated data and organism-specific conventions. MOD rules customize the AI's behavior to respect these conventions - for example, using WormBase anatomy terms (WBbt) for C. elegans or FlyBase allele naming patterns.

### What's the difference between Prompt Explorer's Opus and the main chat?

The main chat uses a multi-agent system optimized for curation tasks. Prompt Explorer's chat uses Claude Opus 4.5 directly, which is better for open-ended discussions about prompts and AI behavior. Opus in Prompt Explorer also has access to the suggestion submission tool.

## Need Help?

If you have questions about using Prompt Explorer or need help formulating feedback, just ask Opus! It's designed to help you navigate prompt engineering concepts and translate your domain expertise into actionable suggestions.
