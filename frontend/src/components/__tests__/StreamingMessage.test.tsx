/**
 * Tests for StreamingMessage component
 * Tests the real-time streaming message display functionality
 */
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import StreamingMessage from "../StreamingMessage";
import { act } from "react-dom/test-utils";

// Mock EventSource for SSE testing
class MockEventSource {
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  readyState: number = 0;

  constructor(url: string) {
    this.url = url;
    this.readyState = 1; // OPEN
  }

  close() {
    this.readyState = 2; // CLOSED
  }

  // Helper to simulate receiving a message
  simulateMessage(data: string) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent("message", { data }));
    }
  }

  // Helper to simulate error
  simulateError() {
    if (this.onerror) {
      this.onerror(new Event("error"));
    }
  }
}

// Replace global EventSource
(global as any).EventSource = MockEventSource;

describe("StreamingMessage Component", () => {
  let mockEventSource: MockEventSource;

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("should render initial loading state", () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    expect(screen.getByTestId("streaming-loading")).toBeInTheDocument();
    expect(screen.getByText(/AI is typing/i)).toBeInTheDocument();
  });

  it("should display streaming text chunks progressively", async () => {
    const { container } = render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    // Get the EventSource instance
    mockEventSource =
      (EventSource as any).mock?.instances?.[0] ||
      new MockEventSource("/chat/stream");

    // Simulate streaming chunks
    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "Hello", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": false}',
      );
    });

    await waitFor(() => {
      expect(screen.getByText(/Hello/)).toBeInTheDocument();
    });

    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": " world", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": false}',
      );
    });

    await waitFor(() => {
      expect(screen.getByText(/Hello world/)).toBeInTheDocument();
    });

    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "!", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": true}',
      );
    });

    await waitFor(() => {
      expect(screen.getByText(/Hello world!/)).toBeInTheDocument();
    });

    // Loading indicator should be gone when complete
    expect(screen.queryByTestId("streaming-loading")).not.toBeInTheDocument();
  });

  it("should show typing indicator during streaming", async () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    // Initially shows typing indicator
    expect(screen.getByTestId("typing-indicator")).toBeInTheDocument();

    mockEventSource = new MockEventSource("/chat/stream");

    // Simulate message completion
    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "Complete message", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": true}',
      );
    });

    await waitFor(() => {
      // Typing indicator should disappear when complete
      expect(screen.queryByTestId("typing-indicator")).not.toBeInTheDocument();
    });
  });

  it("should handle streaming errors gracefully", async () => {
    const onError = jest.fn();

    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
        onError={onError}
      />,
    );

    mockEventSource = new MockEventSource("/chat/stream");

    // Simulate connection error
    act(() => {
      mockEventSource.simulateError();
    });

    await waitFor(() => {
      expect(onError).toHaveBeenCalled();
      expect(screen.getByText(/Error loading response/i)).toBeInTheDocument();
    });
  });

  it("should clean up EventSource on unmount", () => {
    const { unmount } = render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    mockEventSource = new MockEventSource("/chat/stream");
    const closeSpy = jest.spyOn(mockEventSource, "close");

    unmount();

    expect(closeSpy).toHaveBeenCalled();
  });

  it("should display provider and model information", () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="gemini"
        model="gemini-2.0-flash"
        showMetadata={true}
      />,
    );

    expect(screen.getByText(/gemini/i)).toBeInTheDocument();
    expect(screen.getByText(/gemini-2.0-flash/i)).toBeInTheDocument();
  });

  it("should handle rapid consecutive chunks", async () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    mockEventSource = new MockEventSource("/chat/stream");

    // Simulate rapid chunks
    const chunks = ["The", " quick", " brown", " fox", " jumps"];

    act(() => {
      chunks.forEach((chunk, index) => {
        mockEventSource.simulateMessage(
          `data: {"delta": "${chunk}", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": ${index === chunks.length - 1}}`,
        );
      });
    });

    await waitFor(() => {
      expect(screen.getByText("The quick brown fox jumps")).toBeInTheDocument();
    });
  });

  it("should handle empty chunks gracefully", async () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
      />,
    );

    mockEventSource = new MockEventSource("/chat/stream");

    // Simulate empty chunk
    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": false}',
      );
    });

    // Should not crash
    expect(screen.getByTestId("streaming-container")).toBeInTheDocument();

    // Add real content
    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "Real content", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": true}',
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Real content")).toBeInTheDocument();
    });
  });

  it("should handle malformed SSE data", async () => {
    const onError = jest.fn();

    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
        onError={onError}
      />,
    );

    mockEventSource = new MockEventSource("/chat/stream");

    // Simulate malformed JSON
    act(() => {
      mockEventSource.simulateMessage("data: {invalid json}");
    });

    // Should handle gracefully without crashing
    expect(screen.getByTestId("streaming-container")).toBeInTheDocument();

    // Can still receive valid data after error
    act(() => {
      mockEventSource.simulateMessage(
        'data: {"delta": "Valid data", "session_id": "test-session", "provider": "openai", "model": "gpt-4o", "is_complete": true}',
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Valid data")).toBeInTheDocument();
    });
  });

  it("should apply custom className when provided", () => {
    render(
      <StreamingMessage
        sessionId="test-session"
        provider="openai"
        model="gpt-4o"
        className="custom-streaming-class"
      />,
    );

    expect(screen.getByTestId("streaming-container")).toHaveClass(
      "custom-streaming-class",
    );
  });
});
