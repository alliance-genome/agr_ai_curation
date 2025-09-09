# Research: AI Chat Integration

## AI Provider Integration

### OpenAI API Integration

**Decision**: Use OpenAI Python SDK v1.x with streaming support
**Rationale**:

- Existing codebase already imports openai (line 5 in chat.py)
- Native streaming support via `stream=True` parameter
- Well-established patterns for FastAPI integration
  **Alternatives considered**:
- Direct HTTP requests (rejected: more complex error handling)
- LangChain (rejected: adds unnecessary complexity for simple chat)

### Google Gemini API Integration

**Decision**: Use OpenAI Python SDK with Gemini endpoints (OpenAI compatibility mode)
**Rationale**:

- **MAJOR SIMPLIFICATION**: Gemini now supports OpenAI-compatible API endpoints
- Same OpenAI library works for both providers - just change base_url and api_key
- Identical streaming support (`stream=True` parameter works seamlessly)
- Reduces implementation complexity from two different SDKs to one unified approach
  **Alternatives considered**:
- google-generativeai SDK (rejected: unnecessary complexity when OpenAI compatibility available)
- Vertex AI (rejected: requires GCP setup, overkill for development)
- Direct REST API calls (rejected: when OpenAI library works for both)

### Unified Implementation Approach

**Key Implementation Details**:

```python
# Single OpenAI client can handle both providers
from openai import OpenAI

# For OpenAI (default)
openai_client = OpenAI(api_key=openai_api_key)

# For Gemini (OpenAI compatibility mode)
gemini_client = OpenAI(
    api_key=gemini_api_key,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

# Identical API calls for both:
response = client.chat.completions.create(
    model="gpt-4o" or "gemini-2.0-flash",
    messages=[...],
    stream=True  # Works identically for both providers
)
```

**Benefits**:

- Single dependency (openai package)
- Identical error handling patterns
- Same streaming iteration logic
- Reduced testing complexity (one API pattern to test)

## Streaming Response Implementation

### FastAPI Streaming Patterns

**Decision**: Use FastAPI StreamingResponse with Server-Sent Events (SSE)
**Rationale**:

- Frontend already uses axios for HTTP requests
- SSE provides reliable streaming with automatic reconnection
- Well-supported pattern for AI chat applications
  **Alternatives considered**:
- WebSockets (rejected: more complex state management)
- Polling (rejected: inefficient, poor UX)

### Frontend Streaming Handling

**Decision**: Use EventSource API with React state updates
**Rationale**:

- Native browser API, no additional dependencies
- Existing React state patterns can be extended
- Material-UI components already handle dynamic content updates
  **Alternatives considered**:
- Custom WebSocket client (rejected: unnecessary complexity)
- Long polling with setTimeout (rejected: poor UX, inefficient)

## Configuration Management

### API Key Storage

**Decision**: Environment variables in .env file, loaded via FastAPI settings
**Rationale**:

- Existing pattern in codebase (settings.py already exists)
- Secure, not committed to git
- Easy Docker container configuration
  **Alternatives considered**:
- Database storage (rejected: keys shouldn't be in database)
- Config files (rejected: risk of accidental commits)

### Model Selection UI

**Decision**: Material-UI Select component in existing chat interface
**Rationale**:

- Consistent with existing UI components (@mui/material already imported)
- Minimal changes to current ChatInterface.tsx
- Dropdown pattern familiar to users
  **Alternatives considered**:
- Separate settings page (rejected: adds navigation complexity)
- Radio buttons (rejected: takes more space)

## Database Schema Considerations

### Existing Schema Analysis

**Decision**: Extend existing ChatHistory table with optional model_provider and model_name columns
**Rationale**:

- Preserves existing functionality completely
- Allows tracking which model generated each response
- Minimal database migration required
  **Alternatives considered**:
- New AIConfiguration table (rejected: violates constraint of no new tables)
- JSON metadata column (rejected: less queryable)

## Error Handling Patterns

### AI Service Failures

**Decision**: Graceful degradation with fallback to error message
**Rationale**:

- Maintains chat functionality even when AI services are down
- Clear user feedback about service status
- Allows retry mechanisms
  **Alternatives considered**:
- Silent failures (rejected: poor UX)
- Hard failures with exceptions (rejected: breaks chat flow)

### Rate Limiting and Quotas

**Decision**: Client-side retry with exponential backoff
**Rationale**:

- Most AI APIs return clear rate limit headers
- Prevents overwhelming backend with retries
- Good user experience with loading states
  **Alternatives considered**:
- Server-side queuing (rejected: adds complexity)
- Immediate failure (rejected: poor UX)

## Performance Considerations

### Response Time Optimization

**Decision**: Implement response caching for repeated queries (future enhancement)
**Rationale**:

- Common queries in biological curation context can be cached
- Reduces API costs and improves response times
  **Note**: This is identified as a future enhancement beyond the core feature scope

### Token Usage Optimization

**Decision**: Implement conversation context truncation
**Rationale**:

- Prevents exponential token growth in long conversations
- Maintains relevant context while controlling costs
- Standard pattern in AI chat applications

## Development and Testing Strategy

### Local Development Setup

**Decision**: Use API key configuration in docker-compose.yml environment variables
**Rationale**:

- Maintains existing Docker development workflow
- Easy switching between different API keys for testing
- No changes to Docker setup required

### Documentation and Research During Development

**Decision**: Use Context7 MCP server for up-to-date API documentation
**Rationale**:

- Context7 MCP provides access to current OpenAI API documentation and examples
- Real-time access to latest Gemini OpenAI compatibility documentation
- Ensures implementation follows current best practices and patterns
- Reduces risk of using outdated documentation or deprecated methods
  **Usage Pattern**:
- Query Context7 for OpenAI streaming implementation examples
- Verify Gemini OpenAI compatibility endpoint specifications
- Check for any recent API changes or new parameters
- Get current error handling recommendations

### Testing Approach

**Decision**: Mock AI API responses for automated tests, real API for manual testing
**Rationale**:

- Fast, reliable automated test execution
- Real API testing during development and integration testing
- Prevents test failures due to external API issues

## Security Considerations

### API Key Protection

**Decision**: Server-side API key storage only, never expose to frontend
**Rationale**:

- Frontend code is public, API keys must stay private
- Existing backend already handles sensitive configuration
- Standard security practice for API integrations

### Input Sanitization

**Decision**: Use existing FastAPI request validation, add length limits
**Rationale**:

- Existing Pydantic models already validate input
- Need to add reasonable message length limits to prevent abuse
- Consistent with existing codebase patterns
