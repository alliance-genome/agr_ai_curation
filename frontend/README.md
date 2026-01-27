# AI Curation Platform - Frontend

## Overview

React-based frontend application built with Vite, Material-UI, and TypeScript. Provides user interfaces for:
- AI chat interactions with OpenAI Agents
- Document management and processing
- Vector database control panel
- Real-time streaming responses

## Architecture

```
frontend/
├── src/
│   ├── components/         # React components
│   │   ├── Chat/          # Chat interface components
│   │   ├── Documents/     # Document management UI
│   │   └── Common/        # Shared components
│   ├── hooks/             # Custom React hooks
│   ├── services/          # API service layer
│   ├── types/             # TypeScript type definitions
│   ├── utils/             # Utility functions
│   └── App.tsx            # Main application component
├── public/                # Static assets
├── nginx.conf            # Nginx configuration for production
├── Dockerfile            # Multi-stage build configuration
├── package.json          # Dependencies and scripts
├── vite.config.ts        # Vite configuration
└── tsconfig.json         # TypeScript configuration
```

## Technology Stack

### Core
- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool and dev server

### UI Components
- **Material-UI (MUI)** - Component library
- **MUI X Data Grid Pro** - Advanced data tables
- **@emotion** - CSS-in-JS styling

### State & Data
- **React Query (TanStack Query)** - Server state management
- **React Router** - Client-side routing

## Development Setup

### Prerequisites
- Node.js 20+
- npm or yarn

### Local Development

1. Install dependencies:
```bash
cd frontend
npm install
```

2. Configure API endpoint (optional, defaults to proxy):
```bash
# Create .env.local
echo "VITE_API_BASE=http://localhost:8000" > .env.local
```

3. Start development server:
```bash
npm start
# or
npm run dev
```

The app will be available at `http://localhost:5173`

### Development Features
- Hot Module Replacement (HMR)
- TypeScript type checking
- ESLint code linting
- Automatic API proxying to backend

## Building for Production

### Local Build

```bash
# Build production bundle
npm run build

# Preview production build
npm run preview
```

### Docker Build

From the root directory:
```bash
docker-compose build frontend
docker-compose up frontend
```

The app will be served by Nginx at `http://localhost:3001`

## API Integration

### Development Mode
In development, Vite proxies API requests to the backend:
- `/api/*` → `http://localhost:8000/api/*`
- `/weaviate/*` → `http://localhost:8000/weaviate/*`

### Production Mode
Nginx handles API proxying in production:
- Requests to `/api/*` and `/weaviate/*` are forwarded to the backend container
- Server-Sent Events (SSE) are supported for streaming responses

### API Service Layer

```typescript
// Example API service
import { apiClient } from '@/services/api';

// Chat API
const response = await apiClient.post('/api/chat', {
  message: 'Hello, AI!'
});

// Streaming API
const eventSource = new EventSource('/api/chat/stream');
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Handle streaming response
};
```

## Component Structure

### Main Components

- **ChatInterface** - AI chat UI with message history
- **DocumentManager** - Upload and manage documents
- **ChunkViewer** - View document chunks and vectors
- **ProcessingStatus** - Real-time processing status
- **SettingsPanel** - Configuration and settings
- **AuditPanel** - Real-time AI agent activity monitoring (see dedicated section below)

### Shared Components

- **LoadingSpinner** - Loading states
- **ErrorBoundary** - Error handling
- **ConfirmDialog** - User confirmations
- **Snackbar** - Notifications

## Audit Panel Feature

The Audit Panel provides curators with real-time visibility into AI agent activity during conversations. It displays all backend operations transparently to prevent data hallucination concerns.

### Overview

The Audit Panel is located in the right panel's "Audit" tab and shows:
- Supervisor workflow decisions
- Crew dispatches and agent execution
- Database queries (SQL) and API calls
- LLM reasoning steps
- Error states and completion status

### Architecture

**Unified SSE Event Stream**:
- Backend emits structured `AuditEvent` objects via Server-Sent Events
- Frontend filters events: Chat shows subset (user-friendly), Audit shows all (comprehensive)
- Single source of truth eliminates duplication and ensures consistency

**Event Types** (10 total):
1. `SUPERVISOR_START` - Supervisor begins processing user request
2. `SUPERVISOR_DISPATCH` - Supervisor dispatches specific crew
3. `CREW_START` - Crew kickoff begins
4. `AGENT_COMPLETE` - Agent finishes execution
5. `TOOL_START` - Tool usage begins (includes SQL/API details)
6. `TOOL_COMPLETE` - Tool usage finishes
7. `LLM_CALL` - LLM thinking/reasoning
8. `SUPERVISOR_RESULT` - Supervisor receives results from crew
9. `SUPERVISOR_COMPLETE` - Supervisor finishes processing
10. `SUPERVISOR_ERROR` - Supervisor encounters error

### Key Features

- **Real-time updates**: Events appear as they occur during conversation
- **Session scoping**: Events automatically clear when starting a new session
- **Auto-scroll**: Panel smoothly scrolls to bottom on new events
- **Copy to clipboard**: Copy all events as formatted text
- **Manual clear**: Clear button to reset the panel
- **Query transparency**: SQL queries and API parameters displayed inline
- **Severity styling**: Color-coded events (blue=info, green=success, red=error)

### Components

**AuditPanel** (`src/components/AuditPanel.tsx`):
- Main container managing event list and UI state
- Subscribes to unified SSE stream via `useChatStream` hook
- Implements messagesEndRef scroll pattern from Chat component
- Provides copy and clear functionality

**AuditEventItem** (`src/components/AuditEventItem.tsx`):
- Renders individual audit events
- Applies severity-based styling (info/success/error)
- Displays query details for TOOL_START events

**RightPanel** (`src/components/RightPanel.tsx`):
- Tab container with "Audit" and "Tools" tabs
- Preserves AuditPanel state during tab navigation
- Uses MUI Tabs for accessibility

### Helper Functions

Located in `src/utils/auditHelpers.ts`:

- `parseSSEEvent(sseData)` - Converts SSE to AuditEvent with UUID and parsed timestamp
- `formatAuditEvent(event)` - Creates human-readable text for display/copying
- `getEventPrefix(type)` - Returns text prefix like "[SUPERVISOR]" or "[TOOL]"
- `getEventLabel(event)` - Generates detailed, context-aware message
- `getEventSeverity(type)` - Determines styling (info/success/error)

### Display Format

Events are displayed with text prefixes (no emojis):

```
[SUPERVISOR] Processing user query
[SUPERVISOR] Dispatching crew: disease_ontology (step 1/1)
[CREW] Starting crew: Disease Ontology Crew
[TOOL] Searching database...
Query: SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'
[TOOL] Database search complete
[AGENT] Agent completed: Disease Ontology Agent
[SUPERVISOR] Results from disease_ontology (step 1)
[SUPERVISOR] Query completed successfully (1 steps executed)
```

### State Management

- Simple React `useState` for event array (no Redux/complex state)
- Session-scoped (events cleared on session change)
- No persistence across page refreshes (ephemeral by design)
- Tab navigation preserves state via `hidden` prop pattern

### Integration

The audit panel integrates with existing infrastructure:

```typescript
// HomePage.tsx - sessionId and events lifted to parent
import { useState } from 'react'
import { useChatStream } from '../hooks/useChatStream'

function HomePage() {
  // Session ID managed separately via state and API call to /api/chat/session
  const [sessionId, setSessionId] = useState<string | null>(null)

  // Shared SSE stream for both Chat and AuditPanel
  const { events, isLoading, sendMessage } = useChatStream()

  // Events from useChatStream are passed as sseEvents prop to RightPanel
  return (
    <RightPanel sessionId={sessionId} sseEvents={events} />
  )
}
```

### Backend Requirements

Backend must emit structured audit events via SSE:

```python
# backend/src/lib/chat/progress_listener.py
emit_audit_event({
    'type': 'TOOL_START',
    'timestamp': datetime.now().isoformat(),
    'sessionId': session_id,
    'details': {
        'toolName': 'sql_query_tool',
        'friendlyName': 'Searching database...',
        'toolArgs': {'query': 'SELECT * FROM...'}
    }
})
```

See `backend/src/lib/chat/README.md` for backend dispatch dictionary documentation.

### Testing

Tests located in `src/test/components/`:

- `AuditEventItem.test.tsx` - Individual event rendering
- `AuditPanel.test.tsx` - Panel functionality (session, scroll, copy, clear)
- `RightPanel.test.tsx` - Tab navigation and state persistence

Run tests:
```bash
npm test -- AuditPanel
npm test -- AuditEventItem
npm test -- RightPanel
```

### Future Enhancements

Potential improvements (not yet implemented):
- Event filtering by type
- Event search/grep capability
- Timestamp display toggle
- Event export (JSON/CSV)
- Event persistence (if needed)
- Query truncation with expand/collapse

## Testing

```bash
# Run tests
npm test

# Run tests with coverage
npm run test:coverage

# Run tests in watch mode
npm test -- --watch
```

## Code Quality

### Linting
```bash
# Run ESLint
npm run lint

# Fix auto-fixable issues
npm run lint -- --fix
```

### Type Checking
```bash
# Run TypeScript compiler checks
npm run type-check
```

## Nginx Configuration

The production build uses Nginx for:
- Serving static assets with gzip compression
- Proxying API requests to the backend
- Handling client-side routing
- Security headers
- SSE support for streaming

Key features:
- Buffering disabled for SSE endpoints
- Long timeouts for streaming connections
- X-Frame-Options and other security headers
- Efficient caching strategies

## Environment Variables

- `VITE_API_BASE` - Backend API URL (development only)
- `NODE_ENV` - Environment mode (development/production)

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+

## Troubleshooting

### Common Issues

1. **API connection failed**: Check backend is running on port 8000
2. **Blank page**: Check browser console for errors
3. **Styles not loading**: Clear browser cache
4. **TypeScript errors**: Run `npm run type-check`

### Debug Mode

Enable React DevTools:
1. Install React Developer Tools browser extension
2. Open browser DevTools → React tab

View network requests:
- Open browser DevTools → Network tab
- Filter by XHR/Fetch to see API calls

## Project Scripts

- `npm start` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm test` - Run test suite
- `npm run lint` - Run ESLint
- `npm run type-check` - Check TypeScript types

## Contributing

1. Follow React best practices
2. Use TypeScript for type safety
3. Write tests for new components
4. Follow the existing component structure
5. Use Material-UI components consistently

## Performance Optimization

- Code splitting with React.lazy()
- Memoization with React.memo() and useMemo()
- Virtual scrolling for large lists
- Image lazy loading
- Bundle size optimization with Vite