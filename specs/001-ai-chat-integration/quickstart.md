# Quickstart: AI Chat Integration

## Prerequisites

- Docker and Docker Compose installed
- OpenAI API key (for OpenAI provider)
- Google AI API key (for Gemini provider)
- Existing AI curation platform running

## Environment Setup

1. **Configure API Keys**

   ```bash
   # Add to your .env file
   OPENAI_API_KEY=your_openai_api_key_here
   GOOGLE_AI_API_KEY=your_google_ai_api_key_here
   ```

2. **Install Python Dependencies**

   ```bash
   cd backend
   pip install openai google-generativeai
   ```

3. **Install Frontend Dependencies** (if needed)
   ```bash
   cd frontend
   npm install
   ```

## Quick Validation Tests

### Test 1: Basic AI Response (OpenAI)

```bash
# Start the application
docker-compose up

# Test basic chat with OpenAI
curl -X POST "http://localhost:8002/chat/" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, what can you help me with?",
    "provider": "openai",
    "model": "gpt-4o"
  }'
```

**Expected Result**: JSON response with AI-generated text instead of stub message

### Test 2: Model Selection (Gemini)

```bash
# Test chat with Gemini provider
curl -X POST "http://localhost:8002/chat/" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Explain biological curation",
    "provider": "gemini",
    "model": "gemini-2.5-pro"
  }'
```

**Expected Result**: JSON response showing provider="gemini" and model="gemini-2.5-pro"

### Test 3: Streaming Response

```bash
# Test streaming endpoint
curl -X POST "http://localhost:8002/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me about gene ontology",
    "provider": "openai",
    "model": "gpt-4o"
  }'
```

**Expected Result**: Server-Sent Events stream with incremental response chunks

### Test 4: Available Models

```bash
# Get list of available models
curl -X GET "http://localhost:8002/chat/models"
```

**Expected Result**: JSON object listing OpenAI and Gemini models

## Frontend Validation

### Test 5: UI Model Selection

1. Open the application at http://localhost:3000
2. Navigate to the chat interface
3. Look for model selection dropdown
4. Verify options include:
   - OpenAI: gpt-4o, gpt-4o-mini, gpt-3.5-turbo
   - Gemini: gemini-2.5-pro, gemini-1.5-pro, gemini-1.5-flash

### Test 6: Streaming Visual Feedback

1. Select any AI model in the dropdown
2. Type "Explain protein folding" and send
3. Observe:
   - Loading/typing indicator appears
   - Response appears character-by-character (streaming)
   - Message is saved to conversation history
   - No stub response visible

### Test 7: Conversation Persistence

1. Send several messages in a conversation
2. Refresh the page
3. Verify conversation history is preserved
4. Check that model provider/name are displayed with each message

## Error Scenario Tests

### Test 8: Invalid API Key

```bash
# Test with invalid API key (temporarily modify .env)
OPENAI_API_KEY=invalid_key

# Should return graceful error message, not crash
curl -X POST "http://localhost:8002/chat/" \
  -H "Content-Type: application/json" \
  -d '{"message": "test", "provider": "openai"}'
```

**Expected Result**: HTTP 500 with clear error message about API key

### Test 9: Rate Limiting

```bash
# Send many requests rapidly to trigger rate limiting
for i in {1..10}; do
  curl -X POST "http://localhost:8002/chat/" \
    -H "Content-Type: application/json" \
    -d '{"message": "test '$i'", "provider": "openai"}' &
done
```

**Expected Result**: Some requests return rate limit error, frontend handles gracefully

### Test 10: Long Message Validation

```bash
# Test message length limit
curl -X POST "http://localhost:8002/chat/" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "'$(python -c 'print("x" * 10001)')'",
    "provider": "openai"
  }'
```

**Expected Result**: HTTP 422 validation error for message too long

## Database Verification

### Test 11: Message Storage

```sql
-- Connect to PostgreSQL and verify chat storage
SELECT session_id, role, content, model_provider, model_name, timestamp
FROM chathistory
ORDER BY timestamp DESC
LIMIT 10;
```

**Expected Result**: Recent messages show model_provider and model_name populated

### Test 12: Backward Compatibility

```sql
-- Verify existing messages still work (NULL model fields)
SELECT COUNT(*) FROM chathistory WHERE model_provider IS NULL;
```

**Expected Result**: Existing messages remain accessible with NULL model fields

## Performance Verification

### Test 13: Response Time

```bash
# Measure response time
time curl -X POST "http://localhost:8002/chat/" \
  -H "Content-Type: application/json" \
  -d '{"message": "Quick test", "provider": "openai"}'
```

**Expected Result**: Response within 2-3 seconds for short messages

### Test 14: Streaming Latency

- Manual test: Send message and measure time to first character
- **Expected Result**: First streaming chunk within 500ms

## Success Criteria Checklist

- [ ] ✅ Basic AI responses work (no more stub messages)
- [ ] ✅ Model selection dropdown appears in UI
- [ ] ✅ Streaming responses display character-by-character
- [ ] ✅ Conversation history preserves model information
- [ ] ✅ Both OpenAI and Gemini providers work
- [ ] ✅ Error handling shows user-friendly messages
- [ ] ✅ No breaking changes to existing functionality
- [ ] ✅ Docker environment works without modifications
- [ ] ✅ Database stores chat history with model metadata
- [ ] ✅ Frontend shows loading indicators during AI processing

## Troubleshooting

### "No module named 'openai'" Error

```bash
cd backend
pip install openai google-generativeai
# or rebuild Docker containers
docker-compose build
```

### "API key not found" Error

- Check .env file has correct API key format
- Restart Docker containers after .env changes
- Verify API keys are valid by testing directly

### Streaming Not Working

- Check browser console for EventSource errors
- Verify CORS settings allow streaming endpoints
- Test with curl first to isolate frontend vs backend issues

### Frontend Model Dropdown Missing

- Check browser console for JavaScript errors
- Verify ChatInterface component loaded correctly
- Check network tab for failed API calls to /chat/models

## Next Steps After Validation

1. Run full test suite: `pytest backend/tests/`
2. Run frontend tests: `npm test` in frontend directory
3. Performance testing with realistic biological curation queries
4. Load testing with multiple concurrent users
