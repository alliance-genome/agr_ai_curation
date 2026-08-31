from src.analyzers.token_analysis import TokenAnalysisAnalyzer


def test_token_analysis_reports_v2_cache_buckets_and_skips_zero_wrapper():
    observations = [
        {
            "id": "wrapper-1",
            "type": "GENERATION",
            "model": "gpt-5.6-terra",
            "usage_details": {},
            "cost_details": {},
        },
        {
            "id": "provider-1",
            "type": "GENERATION",
            "provided_model_name": "gpt-5.6-terra",
            "startTime": "2026-08-26T12:00:00Z",
            "endTime": "2026-08-26T12:00:01Z",
            "usage_details": {
                "input": 420,
                "output": 110,
                "total": 530,
                "input_tokens.cache_read": 100,
                "input_tokens.cache_write": 20,
                "output_tokens.reasoning": 30,
            },
            "calculated_total_cost": 0.00216,
        },
    ]

    analysis = TokenAnalysisAnalyzer.analyze(
        {"id": "trace-1", "latency": 1.5},
        observations,
    )

    assert analysis["found"] is True
    assert analysis["total_generations"] == 1
    assert analysis["total_prompt_tokens"] == 420
    assert analysis["total_completion_tokens"] == 110
    assert analysis["total_cost"] == 0.00216
    generation = analysis["generations"][0]
    assert generation["uncached_input_tokens"] == 300
    assert generation["cache_read_tokens"] == 100
    assert generation["cache_write_tokens"] == 20
    assert generation["reasoning_tokens"] == 30
    assert generation["cost_source"] == "langfuse_calculated"
    assert generation["estimated_total_cost"] is None
    assert analysis["model_breakdown"]["gpt-5.6-terra"]["count"] == 1


def test_token_analysis_decodes_bounded_provider_usage_without_generation():
    observations = [
        {
            "id": "provider-usage-1",
            "type": "EVENT",
            "metadata": {
                "provider_usage": {
                    "requested_provider": "openrouter",
                    "requested_model": "deepseek/deepseek-v4-pro-0813",
                    "actual_provider": "DeepInfra",
                    "actual_model": "deepseek/deepseek-v4-pro-0813",
                    "routing_attempt": 1,
                    "latency_ms": 1234,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "billed_cost": {
                        "amount": "0.0012300",
                        "unit": "credits",
                        "source": "openrouter_usage",
                        "future": "ignored",
                    },
                    "summary": "ignored",
                    "pipeline": [{"prompt": "ignored"}],
                }
            },
        }
    ]

    analysis = TokenAnalysisAnalyzer.analyze({"id": "trace-1"}, observations)

    assert analysis["found"] is True
    assert analysis["total_generations"] == 0
    assert analysis["total_prompt_tokens"] == 0
    assert analysis["total_completion_tokens"] == 0
    assert analysis["provider_usage"] == [
        {
            "requested_provider": "openrouter",
            "requested_model": "deepseek/deepseek-v4-pro-0813",
            "actual_provider": "DeepInfra",
            "actual_model": "deepseek/deepseek-v4-pro-0813",
            "routing_attempt": 1,
            "latency_ms": 1234,
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
            "billed_cost": {
                "amount": "0.0012300",
                "unit": "credits",
                "source": "openrouter_usage",
            },
        }
    ]
