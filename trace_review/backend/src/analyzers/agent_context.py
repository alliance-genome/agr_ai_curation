"""
Agent Context Analyzer
Extracts agent configuration, instructions, and available tools from traces.

This analyzer helps understand what agents were configured to do and what
tools they had available during execution.
"""
from typing import Dict, List, Any, Optional


class AgentContextAnalyzer:
    """Analyzes agent configuration and context from traces"""

    @classmethod
    def analyze(cls, trace: Dict, observations: List[Dict]) -> Dict:
        """
        Analyze agent configuration and context.

        Args:
            trace: Complete trace data from Langfuse (raw_trace)
            observations: List of all observations

        Returns:
            Dictionary with agent context including:
            - supervisor: Supervisor agent config
            - specialists: List of specialist agent configs
            - all_tools: Combined list of all available tools
            - model_configs: Model configuration per agent
        """
        # Extract raw_trace if wrapped
        raw_trace = trace.get("raw_trace", trace)

        # Get trace-level metadata
        trace_metadata = raw_trace.get("metadata", {})

        # Filter GENERATION observations and sort by time
        generations = [o for o in observations if o.get("type") == "GENERATION"]
        generations.sort(key=lambda x: x.get("startTime", ""))

        if not generations:
            return {
                "found": False,
                "supervisor": None,
                "specialists": [],
                "all_tools": [],
                "model_configs": {}
            }

        # Extract unique agent contexts from generations
        agents = {}
        all_tools = []
        seen_tools = set()

        for gen in generations:
            meta = gen.get("metadata", {})
            model = gen.get("model", "unknown")

            # Extract agent identifier from generation
            # First generation is typically supervisor, subsequent are specialists
            instructions = meta.get("instructions", "")
            tools = meta.get("tools", [])

            # Try to identify agent type from instructions
            agent_type = cls._identify_agent_type(instructions, tools)

            # Extract tools
            for tool in tools:
                tool_name = tool.get("name", "unknown")
                if tool_name not in seen_tools:
                    seen_tools.add(tool_name)
                    all_tools.append({
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                        "strict": tool.get("strict", False)
                    })

            # Store agent config
            agent_key = f"{agent_type}_{model}"
            if agent_key not in agents:
                agents[agent_key] = {
                    "agent_type": agent_type,
                    "model": model,
                    "temperature": meta.get("temperature"),
                    "tool_choice": meta.get("tool_choice"),
                    "reasoning": meta.get("reasoning"),
                    "instructions_length": len(instructions),
                    "instructions_preview": instructions[:500] + "..." if len(instructions) > 500 else instructions,
                    "full_instructions": instructions,  # Full instructions for expanded view
                    "tools_available": [t.get("name") for t in tools],
                    "generation_count": 1
                }
            else:
                agents[agent_key]["generation_count"] += 1

        # Separate supervisor from specialists
        supervisor = None
        specialists = []

        for agent in agents.values():
            if agent["agent_type"] == "supervisor":
                supervisor = agent
            else:
                specialists.append(agent)

        return {
            "found": True,
            "trace_metadata": {
                "supervisor_agent": trace_metadata.get("supervisor_agent"),
                "supervisor_model": trace_metadata.get("supervisor_model"),
                "has_document": trace_metadata.get("has_document")
            },
            "supervisor": supervisor,
            "specialists": specialists,
            "all_tools": all_tools,
            "model_configs": {
                agent["model"]: {
                    "temperature": agent["temperature"],
                    "tool_choice": agent["tool_choice"],
                    "reasoning": agent["reasoning"]
                }
                for agent in agents.values()
            }
        }

    @staticmethod
    def _identify_agent_type(instructions: str, tools: List[Dict]) -> str:
        """
        Identify agent type from instructions and tools.

        Args:
            instructions: Agent instructions text
            tools: List of available tools

        Returns:
            Agent type string (supervisor, pdf_specialist, etc.)
        """
        if not instructions:
            # Try to identify from tools
            tool_names = [t.get("name", "") for t in tools]
            if any("ask_" in name for name in tool_names):
                return "supervisor"
            if any("search_document" in name or "read_section" in name for name in tool_names):
                return "pdf_specialist"
            return "unknown"

        instructions_lower = instructions.lower()

        # Check for specific agent patterns
        if "supervisor" in instructions_lower or "route" in instructions_lower:
            return "supervisor"
        if "pdf" in instructions_lower or "document" in instructions_lower:
            return "pdf_specialist"
        if "gene expression" in instructions_lower:
            return "gene_expression_specialist"
        if "gene" in instructions_lower and "curation" in instructions_lower:
            return "gene_specialist"
        if "allele" in instructions_lower:
            return "allele_specialist"
        if "disease" in instructions_lower:
            return "disease_specialist"
        if "chemical" in instructions_lower:
            return "chemical_specialist"
        if "gene ontology" in instructions_lower or "go term" in instructions_lower:
            return "gene_ontology_specialist"
        if "ortholog" in instructions_lower:
            return "orthologs_specialist"
        if "ontology mapping" in instructions_lower:
            return "ontology_mapping_specialist"

        return "unknown"

    @classmethod
    def extract_full_instructions(cls, observations: List[Dict]) -> Dict[str, str]:
        """
        Extract full instructions text for each unique agent.

        Args:
            observations: List of all observations

        Returns:
            Dictionary mapping agent type to full instructions text
        """
        instructions_by_agent = {}

        for obs in observations:
            if obs.get("type") != "GENERATION":
                continue

            meta = obs.get("metadata", {})
            instructions = meta.get("instructions", "")
            if not instructions:
                continue

            tools = meta.get("tools", [])
            agent_type = cls._identify_agent_type(instructions, tools)

            if agent_type not in instructions_by_agent:
                instructions_by_agent[agent_type] = instructions

        return instructions_by_agent

    @classmethod
    def get_tool_details(cls, observations: List[Dict], tool_name: str) -> Optional[Dict]:
        """
        Get detailed information about a specific tool.

        Args:
            observations: List of all observations
            tool_name: Name of the tool to look up

        Returns:
            Tool details dictionary or None if not found
        """
        for obs in observations:
            if obs.get("type") != "GENERATION":
                continue

            meta = obs.get("metadata", {})
            tools = meta.get("tools", [])

            for tool in tools:
                if tool.get("name") == tool_name:
                    return {
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                        "strict": tool.get("strict", False),
                        "type": tool.get("type", "function")
                    }

        return None
