"""
Agent Configuration Analyzer
Extracts agent configurations logged to Langfuse traces via EVENT observations.

Each agent logs its configuration using log_agent_config() which creates an EVENT
with name pattern: {agent_name}_config (e.g., "pdf_specialist_config")

The event input contains:
- agent_name: Name of the agent (e.g., "PDF Specialist")
- instructions: Full system prompt/instructions
- model: Model name (e.g., "gpt-5-mini")
- tools: List of tool names available to the agent
- model_settings: Dict with temperature, reasoning, tool_choice
- metadata: Optional additional metadata (document_id, hierarchy, etc.)
"""
from typing import Dict, List, Any, Optional


class AgentConfigAnalyzer:
    """Analyzes agent configuration events from trace observations"""

    @staticmethod
    def extract_agent_configs(observations: List[Dict]) -> Dict[str, Any]:
        """
        Extract all agent configuration events from trace observations.

        Agent configs are logged as EVENT type observations with name ending in "_config".
        The input field contains the full configuration details.

        Args:
            observations: List of all observations from trace

        Returns:
            Dictionary with:
            - agents: List of agent configurations
            - agent_count: Number of agents found
            - models_used: List of unique models
            - tools_available: List of all unique tools across agents
        """
        configs = []
        models_used = set()
        tools_available = set()

        for obs in observations:
            obs_type = obs.get("type", "")
            obs_name = obs.get("name", "")

            # Look for EVENT type observations with name ending in "_config"
            if obs_type == "EVENT" and obs_name.endswith("_config"):
                input_data = obs.get("input", {})

                if not isinstance(input_data, dict):
                    continue

                # Extract agent configuration
                agent_name = input_data.get("agent_name", obs_name.replace("_config", ""))
                instructions = input_data.get("instructions", "")
                model = input_data.get("model", "unknown")
                tools = input_data.get("tools", [])
                model_settings = input_data.get("model_settings", {})
                metadata = input_data.get("metadata", {})

                # Track unique models and tools
                models_used.add(model)
                for tool in tools:
                    tools_available.add(tool)

                # Compute instruction stats
                instruction_stats = AgentConfigAnalyzer._compute_instruction_stats(instructions)

                config = {
                    "agent_name": agent_name,
                    "event_name": obs_name,
                    "model": model,
                    "tools": tools,
                    "model_settings": model_settings,
                    "metadata": metadata,
                    "instructions": instructions,
                    "instruction_stats": instruction_stats,
                    "observation_id": obs.get("id"),
                    "timestamp": obs.get("startTime") or obs.get("start_time")
                }

                configs.append(config)

        # Sort by timestamp if available
        configs.sort(key=lambda x: str(x.get("timestamp", "") or ""))

        return {
            "agents": configs,
            "agent_count": len(configs),
            "models_used": sorted(list(models_used)),
            "tools_available": sorted(list(tools_available))
        }

    @staticmethod
    def _compute_instruction_stats(instructions: str) -> Dict[str, Any]:
        """Compute statistics about the instruction text"""
        if not instructions:
            return {
                "char_count": 0,
                "word_count": 0,
                "line_count": 0,
                "has_markdown_headings": False,
                "has_code_blocks": False,
                "has_bullet_points": False
            }

        lines = instructions.split("\n")
        words = instructions.split()

        return {
            "char_count": len(instructions),
            "word_count": len(words),
            "line_count": len(lines),
            "has_markdown_headings": any(line.strip().startswith("#") for line in lines),
            "has_code_blocks": "```" in instructions,
            "has_bullet_points": any(line.strip().startswith(("- ", "* ", "• ")) for line in lines)
        }

    @staticmethod
    def get_agent_by_name(configs: Dict[str, Any], agent_name: str) -> Optional[Dict]:
        """
        Get a specific agent's configuration by name.

        Args:
            configs: Output from extract_agent_configs()
            agent_name: Name of the agent to find (case-insensitive)

        Returns:
            Agent configuration dict or None if not found
        """
        for agent in configs.get("agents", []):
            if agent.get("agent_name", "").lower() == agent_name.lower():
                return agent
        return None

    @staticmethod
    def summarize_agents(configs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate a summary of all agents without full instructions.

        Useful for quick overview without the large instruction text.

        Args:
            configs: Output from extract_agent_configs()

        Returns:
            List of agent summaries with name, model, tools, and instruction stats
        """
        summaries = []
        for agent in configs.get("agents", []):
            summary = {
                "agent_name": agent.get("agent_name"),
                "model": agent.get("model"),
                "tools": agent.get("tools"),
                "instruction_stats": agent.get("instruction_stats"),
                "metadata_keys": list(agent.get("metadata", {}).keys()) if agent.get("metadata") else []
            }
            summaries.append(summary)
        return summaries
