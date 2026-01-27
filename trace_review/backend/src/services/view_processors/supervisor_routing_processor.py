"""
Supervisor Routing View Processor
Extracts supervisor routing decisions from trace observations
"""
import json
import re
from typing import Dict, Any, Optional, List


def parse_supervisor_json(raw_output: str) -> Optional[Dict[str, Any]]:
    """
    Parse JSON from supervisor output, handling markdown code blocks

    Args:
        raw_output: Raw output string from supervisor observation

    Returns:
        Parsed JSON dict or None if parsing fails
    """
    if not raw_output or not isinstance(raw_output, str):
        return None

    # Find JSON content (may be in markdown code block)
    json_start = raw_output.find('{')
    json_end = raw_output.rfind('}') + 1

    if json_start == -1 or json_end <= json_start:
        return None

    json_str = raw_output[json_start:json_end]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def extract_supervisor_decisions(observations: List[Dict]) -> List[Dict[str, Any]]:
    """
    Extract all supervisor routing decisions from observations

    Args:
        observations: List of observation dictionaries

    Returns:
        List of routing decision dictionaries with metadata
    """
    decisions = []

    for obs in observations:
        # Only process GENERATION observations with "Supervisor" in the name
        if obs.get('type') != 'GENERATION':
            continue

        if 'Supervisor' not in obs.get('name', ''):
            continue

        # Extract raw output
        output = obs.get('output', {})
        if not isinstance(output, dict):
            continue

        raw = output.get('raw', '')
        if not raw:
            continue

        # Parse supervisor decision JSON
        decision = parse_supervisor_json(raw)
        if not decision:
            continue

        # Add metadata
        decision_with_meta = {
            'observation_id': obs.get('id'),
            'observation_name': obs.get('name'),
            'timestamp': obs.get('startTime'),
            'model': obs.get('model'),
            'latency_ms': obs.get('latency'),
            'tokens': obs.get('usage', {}).get('total', 0),
            'cost': obs.get('calculatedTotalCost', 0),
            **decision  # Merge decision data
        }

        decisions.append(decision_with_meta)

    return decisions


def process_supervisor_routing(trace_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process trace data to extract supervisor routing information

    Args:
        trace_data: Complete trace data from TraceExtractor

    Returns:
        Structured data for supervisor routing view
    """
    observations = trace_data.get('observations', [])

    # Extract all supervisor decisions
    all_decisions = extract_supervisor_decisions(observations)

    if not all_decisions:
        return {
            'found': False,
            'message': 'No supervisor routing decisions found in this trace'
        }

    # Separate initial routing from final synthesis
    initial_routing = None
    final_synthesis = None
    intermediate_decisions = []

    for decision in all_decisions:
        actor = decision.get('actor', '')

        # Check if this is initial supervisor routing
        if actor == 'supervisor' and decision.get('destination') == 'pdf_extraction':
            if not initial_routing or decision.get('timestamp', '') < initial_routing.get('timestamp', ''):
                initial_routing = decision

        # Check if this is final synthesis
        elif actor == 'synthesis_agent' and decision.get('destination') == 'direct_response':
            if not final_synthesis or decision.get('timestamp', '') > final_synthesis.get('timestamp', ''):
                final_synthesis = decision

        # Everything else is intermediate
        else:
            intermediate_decisions.append(decision)

    # Build response
    result = {
        'found': True,
        'initial_routing': initial_routing,
        'final_synthesis': final_synthesis,
        'intermediate_decisions': intermediate_decisions,
        'total_decisions': len(all_decisions)
    }

    # If we have initial routing, extract its key fields for easy display
    if initial_routing:
        result['routing_decision'] = {
            'actor': initial_routing.get('actor', 'N/A'),
            'destination': initial_routing.get('destination', 'N/A'),
            'confidence': initial_routing.get('confidence', 0),
            'query_type': initial_routing.get('query_type', 'N/A'),
            'timestamp': initial_routing.get('timestamp')
        }

        result['reasoning'] = initial_routing.get('reasoning', 'N/A')

        result['routing_plan'] = initial_routing.get('routing_plan', {
            'needs_pdf': False,
            'ontologies_needed': [],
            'genes_to_lookup': [],
            'execution_order': []
        })

        result['immediate_response'] = initial_routing.get('immediate_response')

    # If we have final synthesis, extract its key fields
    if final_synthesis:
        result['synthesis'] = {
            'final_response': final_synthesis.get('final_response'),
            'sources_used': final_synthesis.get('sources_used', []),
            'confidence_level': final_synthesis.get('confidence_level', 0),
            'timestamp': final_synthesis.get('timestamp')
        }

    return result
