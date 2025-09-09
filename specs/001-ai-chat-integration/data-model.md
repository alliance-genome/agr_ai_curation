# Data Model: AI Chat Integration

## Existing Entities (No Changes Required)

### ChatHistory

**Purpose**: Stores conversation messages and responses  
**Location**: `backend/app/models/chat.py` (existing)
**Fields**:

- `id`: Primary key (existing)
- `session_id`: Groups related messages (existing)
- `role`: "user" or "assistant" (existing)
- `content`: Message text (existing)
- `timestamp`: When message was created (existing)

**Extensions Needed**:

- `model_provider`: Optional string (e.g., "openai", "gemini") - NEW
- `model_name`: Optional string (e.g., "gpt-4o", "gemini-2.5-pro") - NEW

**Rationale**:

- Preserves existing functionality completely
- Enables tracking which AI model generated responses
- Optional fields ensure backward compatibility

## New Configuration Entities

### AIConfiguration

**Purpose**: User's AI provider and model preferences
**Location**: In-memory/session storage (not database)
**Fields**:

- `provider`: "openai" | "gemini"
- `model`: string (provider-specific model name)
- `api_key`: string (server-side only, from environment)

**Rationale**:

- No database changes required (constraint compliance)
- Configuration is session-based, not persistent
- API keys managed securely on server

## API Request/Response Models

### Enhanced ChatRequest (Extends Existing)

```python
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = []
    session_id: Optional[str] = None
    # NEW FIELDS:
    provider: Optional[str] = "openai"  # default to OpenAI
    model: Optional[str] = "gpt-4o"     # default model
```

### Enhanced ChatResponse (Extends Existing)

```python
class ChatResponse(BaseModel):
    response: str
    session_id: str
    # NEW FIELDS:
    provider: str           # which provider was used
    model: str              # which model was used
    is_streaming: bool      # whether this is a streaming response
```

### New StreamingChatResponse

```python
class StreamingChatResponse(BaseModel):
    delta: str              # incremental text chunk
    session_id: str
    provider: str
    model: str
    is_complete: bool       # true for final chunk
```

## Validation Rules

### Message Content

- **Max Length**: 10,000 characters (prevent abuse)
- **Required**: Non-empty after stripping whitespace
- **Sanitization**: Basic HTML escape (existing FastAPI validation)

### Provider Selection

- **Valid Providers**: "openai", "gemini"
- **Default**: "openai" if not specified
- **Validation**: Must be in allowed list

### Model Selection

- **OpenAI Models**: "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"
- **Gemini Models** (via OpenAI compatibility): "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"
- **Validation**: Must match provider's available models
- **Note**: Gemini models accessed via OpenAI-compatible endpoints using same client

### Session Management

- **Session ID**: UUID v4 format (existing validation)
- **Auto-generation**: If not provided, create new UUID
- **Persistence**: Session continues across requests with same ID

## State Transitions

### Chat Message Flow

```
1. User sends message with provider/model selection
2. System validates request parameters
3. Message stored as "user" role in ChatHistory
4. AI API called with conversation context
5. Streaming response chunks sent to frontend
6. Complete response stored as "assistant" role
7. Response includes provider/model metadata
```

### Error State Handling

- **API Failure**: Store error message as assistant response
- **Invalid Model**: Fall back to default model for provider
- **Rate Limiting**: Return rate limit error, client handles retry
- **Timeout**: Return timeout error with retry suggestion

## Frontend State Management

### Chat Interface State

```typescript
interface ChatState {
  messages: ChatMessage[];
  currentResponse: string;
  isStreaming: boolean;
  selectedProvider: "openai" | "gemini";
  selectedModel: string;
  error: string | null;
}
```

### Model Selection State

```typescript
interface ModelConfig {
  provider: "openai" | "gemini";
  availableModels: string[];
  selectedModel: string;
}
```

## Database Migration (Minimal)

### Required Changes to ChatHistory Table

```sql
ALTER TABLE chathistory
ADD COLUMN model_provider VARCHAR(50) DEFAULT NULL,
ADD COLUMN model_name VARCHAR(100) DEFAULT NULL;
```

**Impact**:

- Backward compatible (NULL values for existing records)
- No breaking changes to existing queries
- Minimal schema modification

## Caching Strategy (Future Enhancement)

### Response Cache Structure

```python
class ChatCache:
    key: str              # hash of message + context
    response: str         # cached AI response
    provider: str         # which provider generated it
    model: str            # which model generated it
    expires_at: datetime  # cache expiration
```

**Note**: This is planned for future implementation, not part of initial feature scope.
