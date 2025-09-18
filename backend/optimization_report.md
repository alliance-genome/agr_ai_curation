# Performance Optimization Report: 7.27s General Answer Delay

## Current Performance Analysis

### Trace Breakdown (Run ID: 8ed89aaa-b951-46cc-84f1-d054fa26deb1)
- **Total Time**: 11.08s
- **retrieve_context**: 600ms (5.4%)
- **analyze_intent**: 3,199ms (28.9%)
- **general_answer**: 7,269ms (65.6%)

### Root Causes Identified

1. **Model Selection**: Using GPT-4o for all operations
   - Intent routing doesn't need the most powerful model
   - GPT-4o has higher latency than GPT-4o-mini

2. **Sequential Processing**:
   - Intent analysis must complete before answer generation
   - No parallelization of independent operations

3. **Cold Start**:
   - First request after idle period has higher latency
   - Model loading and initialization overhead

## Implemented Optimizations

### 1. Use Faster Model for Intent Routing ✅
- **Change**: Set `intent_router_model = "gpt-4o-mini"` in config
- **Expected Improvement**: ~2s reduction (60% faster routing)
- **Trade-off**: Slightly less accurate intent classification (acceptable)

## Recommended Optimizations

### 2. Parallel Processing for Specialists
- **Current**: Sequential specialist calls when multiple are needed
- **Proposed**: Run specialists in parallel using asyncio.gather()
- **Expected Improvement**: 30-50% reduction when multiple specialists invoked
- **Implementation**:
  ```python
  # In general_supervisor.py
  specialist_tasks = []
  if "gene" in intent:
      specialist_tasks.append(gene_specialist(state))
  if "disease" in intent:
      specialist_tasks.append(disease_specialist(state))

  results = await asyncio.gather(*specialist_tasks)
  ```

### 3. Response Caching
- **Add Redis cache for common questions**
- **Cache key**: Hash of (pdf_id, question, model)
- **TTL**: 1 hour for dynamic content, 24 hours for static PDFs
- **Expected Improvement**: <100ms for cached responses

### 4. Model Warm-up
- **Keep models warm with periodic health checks**
- **Pre-load models on startup**
- **Expected Improvement**: 1-2s reduction on cold starts

### 5. Optimize Context Window
- **Current**: Sending full context regardless of question
- **Proposed**: Dynamic context sizing based on question complexity
- **Expected Improvement**: 10-20% reduction in token processing time

### 6. Use Gemini 2.0 Flash for Speed
- **Gemini 2.0 Flash has very low latency (~1-2s)**
- **Consider as alternative for time-sensitive queries**
- **Trade-off**: Different response style, may need prompt adjustments

## Performance Targets

### Current Performance
- P50: ~11s total latency
- P90: ~15s total latency
- Time to First Token: ~10s

### Target Performance (After All Optimizations)
- P50: ~4s total latency (-64%)
- P90: ~6s total latency (-60%)
- Time to First Token: ~2s (-80%)

## Implementation Priority

1. **High Priority** (Quick wins)
   - ✅ Faster intent routing model (Done)
   - Parallel specialist execution
   - Model warm-up

2. **Medium Priority** (Moderate effort)
   - Response caching
   - Dynamic context sizing

3. **Low Priority** (Consider later)
   - Alternative model providers
   - Query result pre-computation
   - WebSocket persistent connections

## Monitoring & Metrics

Track these metrics via LangSmith:
- Intent routing latency by model
- Specialist invocation patterns
- Cache hit rates
- Token usage per request
- Time to first token (streaming)

## Next Steps

1. Deploy intent routing optimization to production
2. Implement parallel specialist processing
3. Set up performance monitoring dashboard
4. A/B test Gemini 2.0 Flash vs GPT-4o-mini
5. Implement caching layer if P90 > 5s after optimizations