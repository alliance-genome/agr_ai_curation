"""
Supervisor Routing Analyzer
Extracts and visualizes supervisor routing decisions from traces
"""
import json
from typing import Dict, List, Optional


class SupervisorRoutingAnalyzer:
    """Analyzes supervisor routing decisions in traces"""

    @staticmethod
    def parse_supervisor_json(raw_output: str) -> Optional[Dict]:
        """
        Parse JSON from supervisor output, handling markdown code blocks

        Args:
            raw_output: Raw output string from supervisor observation

        Returns:
            Parsed JSON dict or None if parsing fails
        """
        if not raw_output or not isinstance(raw_output, str):
            return None

        # Find JSON content (may be in markdown code block or raw JSON)
        json_start = raw_output.find('{')
        json_end = raw_output.rfind('}') + 1

        if json_start == -1 or json_end <= json_start:
            return None

        json_str = raw_output[json_start:json_end]

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def extract_raw_from_output(output: any) -> Optional[str]:
        """Extract raw field from observation output"""
        if not output:
            return None

        try:
            if isinstance(output, str):
                output_data = json.loads(output)
            elif isinstance(output, dict):
                output_data = output
            else:
                return None

            return output_data.get("raw", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            return None

    @classmethod
    def find_all_supervisor_decisions(cls, observations: List[Dict]) -> List[Dict]:
        """
        Find ALL supervisor routing decisions in trace (initial + intermediate + synthesis)

        Args:
            observations: List of all observations

        Returns:
            List of dictionaries containing supervisor decisions with metadata
        """
        decisions = []

        # Pre-process to map parents to children (for model extraction)
        children_by_parent = {}
        for obs in observations:
            p_id = obs.get("parentObservationId")
            if p_id:
                if p_id not in children_by_parent:
                    children_by_parent[p_id] = []
                children_by_parent[p_id].append(obs)

        for obs in observations:
            # Check GENERATION, CHAIN, and AGENT observations
            if obs.get("type") not in ["GENERATION", "CHAIN", "AGENT"]:
                continue

            # Extract raw output
            raw = cls.extract_raw_from_output(obs.get("output"))
            if not raw:
                continue

            # Parse JSON from raw output
            decision = cls.parse_supervisor_json(raw)
            if not decision:
                continue

            # Check if this looks like a routing decision (has actor or destination)
            # This catches supervisor, pdf_specialist, synthesis_agent, etc.
            if not (decision.get("actor") or decision.get("destination")):
                continue

            # Extract model
            model = "N/A"
            # 1. Try direct on observation
            if obs.get("model"):
                 model = obs.get("model")
            elif obs.get("modelParameters") and isinstance(obs.get("modelParameters"), dict):
                 model = obs["modelParameters"].get("model", "N/A")
            elif obs.get("metadata", {}).get("model"):
                 model = obs["metadata"]["model"]
            elif obs.get("metadata", {}).get("attributes", {}).get("llm.model_name"):
                 model = obs["metadata"]["attributes"]["llm.model_name"]

            # 2. If not found, look at children (if this is an AGENT/CHAIN)
            if model == "N/A" and obs.get("id") in children_by_parent:
                for child in children_by_parent[obs.get("id")]:
                    if child.get("type") == "GENERATION":
                        child_model = child.get("model") or \
                                      (child.get("modelParameters") or {}).get("model") or \
                                      (child.get("metadata") or {}).get("model") or \
                                      ((child.get("metadata") or {}).get("attributes") or {}).get("llm.model_name")
                        if child_model:
                            model = child_model
                            break # Found one, good enough

            # Add metadata from observation
            decision_with_meta = {
                "observation_id": obs.get("id"),
                "observation_name": obs.get("name"),
                "observation_type": obs.get("type"),
                "timestamp": obs.get("startTime"),
                "model": model,
                **decision  # Merge decision data
            }

            decisions.append(decision_with_meta)

        return decisions

    @classmethod
    def deduplicate_decisions(cls, decisions: List[Dict]) -> List[Dict]:
        """
        Remove duplicate decisions (same content from different observations)

        CrewAI creates multiple observations for the same decision:
        - Crew_<uuid>.kickoff (CHAIN)
        - TaskName._execute_core (GENERATION/AGENT)

        We deduplicate by keeping only unique (actor, destination, reasoning) tuples.
        Prefer decisions with valid models and _execute_core observations.

        Args:
            decisions: List of all decisions

        Returns:
            Deduplicated list of decisions
        """
        seen = {}

        for decision in decisions:
            # Create a unique key based on decision content (NOT timestamp, NOT model)
            actor = decision.get("actor", "")
            destination = decision.get("destination", "")
            reasoning = decision.get("reasoning", "")
            final_response = decision.get("final_response", "")
            
            # Key uses content only
            key = (actor, destination, reasoning[:200], final_response[:200])

            if key not in seen:
                seen[key] = decision
            else:
                existing = seen[key]
                should_replace = False
                
                # 1. Prefer having a model
                if decision.get("model") != "N/A" and existing.get("model") == "N/A":
                    should_replace = True
                # 2. If model status equivalent, prefer _execute_core over kickoff
                elif (decision.get("model") != "N/A") == (existing.get("model") != "N/A"):
                     obs_name = decision.get("observation_name", "")
                     existing_name = existing.get("observation_name", "")
                     if "_execute_core" in obs_name and "kickoff" in existing_name:
                         should_replace = True
                
                if should_replace:
                    seen[key] = decision
                # If not replacing, but current has model and existing doesn't, backfill it
                elif existing.get("model") == "N/A" and decision.get("model") != "N/A":
                    existing["model"] = decision["model"]

        return list(seen.values())

    @classmethod
    def categorize_decisions(cls, decisions: List[Dict]) -> Dict:
        """
        Categorize decisions into initial routing, sub-supervisor routing, and final synthesis

        Args:
            decisions: List of all supervisor decisions

        Returns:
            Dictionary with categorized decisions
        """
        # Deduplicate first to remove CrewAI observation duplicates
        decisions = cls.deduplicate_decisions(decisions)

        initial_routing = None
        final_synthesis = None
        sub_supervisor_routing = []

        for decision in decisions:
            actor = decision.get("actor", "")
            destination = decision.get("destination", "")

            # Initial routing: supervisor actor
            if "supervisor" in actor.lower():
                if not initial_routing or decision.get("timestamp", "") < initial_routing.get("timestamp", ""):
                    initial_routing = decision

            # Final synthesis: synthesis_agent with direct_response destination
            elif actor == "synthesis_agent" and destination == "direct_response":
                if not final_synthesis or decision.get("timestamp", "") > final_synthesis.get("timestamp", ""):
                    final_synthesis = decision

            # Sub-supervisor routing: pdf_specialist, internal_db, external_db, etc.
            else:
                sub_supervisor_routing.append(decision)

        return {
            "initial_routing": initial_routing,
            "final_synthesis": final_synthesis,
            "sub_supervisor_routing": sub_supervisor_routing
        }

    @classmethod
    def analyze(cls, observations: List[Dict]) -> Dict:
        """
        Complete analysis of supervisor routing

        Args:
            observations: List of all observations from trace

        Returns:
            Complete routing analysis with all decisions
        """
        # Find all supervisor decisions
        all_decisions = cls.find_all_supervisor_decisions(observations)

        if not all_decisions:
            return {
                "found": False,
                "reasoning": None,
                "routing_plan": None,
                "metadata": None,
                "immediate_response": None
            }

        # Categorize decisions
        categorized = cls.categorize_decisions(all_decisions)
        initial = categorized["initial_routing"]
        final = categorized["final_synthesis"]
        sub_supervisors = categorized["sub_supervisor_routing"]

        # If we have initial routing, extract its fields
        if initial:
            routing_plan = initial.get("routing_plan", {})

            result = {
                "found": True,
                "reasoning": initial.get("reasoning", "N/A"),
                "model": initial.get("model", "N/A"),
                "routing_plan": {
                    "needs_pdf": routing_plan.get("needs_pdf", False),
                    "ontologies_needed": routing_plan.get("ontologies_needed", []),
                    "genes_to_lookup": routing_plan.get("genes_to_lookup", []),
                    "execution_order": routing_plan.get("execution_order", [])
                },
                "metadata": {
                    "destination": initial.get("destination", "N/A"),
                    "confidence": initial.get("confidence", "N/A"),
                    "query_type": initial.get("query_type", "N/A")
                },
                "immediate_response": initial.get("immediate_response"),
                "sub_supervisor_routing": sub_supervisors  # NEW: Add sub-supervisor routing
            }

            # Add final synthesis if available
            if final:
                result["final_synthesis"] = {
                    "final_response": final.get("final_response"),
                    "sources_used": final.get("sources_used", []),
                    "confidence_level": final.get("confidence_level", "N/A"),
                    "model": final.get("model", "N/A")
                }

            return result

        # Fallback if no proper initial routing found
        return {
            "found": False,
            "reasoning": None,
            "routing_plan": None,
            "metadata": None,
            "immediate_response": None,
            "sub_supervisor_routing": []
        }
