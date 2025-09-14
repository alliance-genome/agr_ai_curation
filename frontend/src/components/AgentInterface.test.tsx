import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AgentInterface from "./AgentInterface";

// Mock axios
vi.mock("axios", () => ({
  default: {
    post: vi.fn(),
    get: vi.fn().mockResolvedValue({
      data: {
        openai: ["gpt-4o", "gpt-4o-mini"],
        google: ["gemini-2.0-flash-exp"],
        anthropic: [],
      },
    }),
  },
}));

// Mock ModelSelector to avoid fetch issues in tests
vi.mock("./ModelSelector", () => ({
  default: vi.fn(({ onModelChange, disabled }) => (
    <div data-testid="model-selector">
      <button
        onClick={() => onModelChange("openai", "gpt-4o")}
        disabled={disabled}
      >
        Select Model
      </button>
    </div>
  )),
}));

// Mock fetch for SSE
global.fetch = vi.fn();

describe("AgentInterface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders without crashing", () => {
    render(<AgentInterface />);
    expect(screen.getByText("BioCuration Agent")).toBeInTheDocument();
  });

  it("handles text_delta streaming updates correctly", async () => {
    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text_delta","content":"Hello"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text_delta","content":" world"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"complete","session_id":"test-session"}\n\n',
          ),
        })
        .mockResolvedValueOnce({ done: true }),
    };

    const mockResponse = {
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Type a message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });

    // Send the message
    const sendButton = screen.getByRole("button", { name: /send/i });
    fireEvent.click(sendButton);

    // Wait for streaming to complete
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });
  });

  it("handles full text streaming updates correctly", async () => {
    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text","content":"First update"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text","content":"Second update"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"complete","session_id":"test-session"}\n\n',
          ),
        })
        .mockResolvedValueOnce({ done: true }),
    };

    const mockResponse = {
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Type and send a message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // Should show the latest full text update
    await waitFor(() => {
      expect(screen.getByText("Second update")).toBeInTheDocument();
    });
  });

  it("stores and uses message history from streaming", async () => {
    const mockHistory = [
      { role: "user", content: "Previous message" },
      { role: "assistant", content: "Previous response" },
    ];

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text_delta","content":"New response"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            `data: {"type":"history","metadata":{"messages":${JSON.stringify(mockHistory)}}}\n\n`,
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"complete","session_id":"test-session"}\n\n',
          ),
        })
        .mockResolvedValueOnce({ done: true }),
    };

    const mockResponse = {
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Send first message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "First message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText("New response")).toBeInTheDocument();
    });

    // Send second message - should include history
    fireEvent.change(input, { target: { value: "Second message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      const fetchCall = (global.fetch as any).mock.calls[1];
      const requestBody = JSON.parse(fetchCall[1].body);
      expect(requestBody.message_history).toEqual(mockHistory);
    });
  });

  it("handles entity and annotation streaming updates", async () => {
    const mockEntity = {
      text: "p53",
      type: "gene",
      confidence: 0.95,
    };

    const mockAnnotation = {
      text: "important finding",
      color: "yellow",
      category: "key_finding",
      confidence: 0.9,
    };

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"text_delta","content":"Found p53 gene"}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            `data: {"type":"entity","content":"p53","metadata":${JSON.stringify(mockEntity)}}\n\n`,
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            `data: {"type":"annotation","content":"important","metadata":${JSON.stringify(mockAnnotation)}}\n\n`,
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"complete","session_id":"test-session"}\n\n',
          ),
        })
        .mockResolvedValueOnce({ done: true }),
    };

    const mockResponse = {
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Send message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Analyze this text" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // Wait for entities and annotations to be displayed
    await waitFor(() => {
      expect(screen.getByText(/p53 \(gene\)/)).toBeInTheDocument();
    });
  });

  it("clears message history when conversation is cleared", () => {
    render(<AgentInterface />);

    // Add a message first
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });

    // Clear conversation
    const clearButton = screen.getByRole("button", { name: /clear/i });
    fireEvent.click(clearButton);

    // Check that the UI is reset
    expect(screen.getByText("Start a biocuration session")).toBeInTheDocument();
  });

  it("handles non-streaming responses with message history", async () => {
    const axios = await import("axios");
    const mockResponse = {
      data: {
        session_id: "test-session",
        output: {
          response: "Test response",
          entities: [],
          annotations: [],
          confidence: 0.9,
          requires_review: false,
        },
        message_history: [
          { role: "user", content: "Test message" },
          { role: "assistant", content: "Test response" },
        ],
      },
    };

    (axios.default.post as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Disable streaming - find the Stream toggle button
    const toggleButtons = screen.getAllByRole("button");
    const streamToggle = toggleButtons.find(
      (btn) =>
        btn.getAttribute("value") === "stream" ||
        btn.getAttribute("aria-pressed") === "true",
    );
    if (streamToggle) fireEvent.click(streamToggle);

    // Send message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText("Test response")).toBeInTheDocument();
    });

    // Send another message - should include history
    fireEvent.change(input, { target: { value: "Follow-up message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      const postCall = (axios.default.post as any).mock.calls[1];
      expect(postCall[1].message_history).toEqual(
        mockResponse.data.message_history,
      );
    });
  });

  it("handles streaming errors gracefully", async () => {
    const mockResponse = {
      ok: false,
      status: 500,
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Send message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // Should show error
    await waitFor(() => {
      expect(screen.getByText(/error occurred/i)).toBeInTheDocument();
    });
  });

  it("handles event type messages", async () => {
    const consoleSpy = vi.spyOn(console, "log");

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"event","content":"Processing","metadata":{"step":1}}\n\n',
          ),
        })
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode(
            'data: {"type":"complete","session_id":"test-session"}\n\n',
          ),
        })
        .mockResolvedValueOnce({ done: true }),
    };

    const mockResponse = {
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    };

    (global.fetch as any).mockResolvedValue(mockResponse);

    render(<AgentInterface />);

    // Send message
    const input = screen.getByPlaceholderText(/Ask about genes/);
    fireEvent.change(input, { target: { value: "Test message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith("Agent event:", "Processing", {
        step: 1,
      });
    });
  });
});
