import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import ChatInterface from "../ChatInterface";

describe("ChatInterface", () => {
  const pdfId = "pdf-123";

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a session and sends question", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "session-1" }),
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-type" ? "application/json" : null,
      },
    });

    const encoder = new TextEncoder();
    const chunks = [
      encoder.encode('data: {"type":"start"}\n\n'),
      encoder.encode(
        'data: {"type":"final","answer":"BRCA1 repairs DNA.","citations":[{"page":3}]}\n\n',
      ),
      encoder.encode('data: {"type":"end"}\n\n'),
    ];
    const reader = {
      read: vi.fn(() => {
        const value = chunks.shift();
        if (value) {
          return Promise.resolve({ value, done: false });
        }
        return Promise.resolve({ value: undefined, done: true });
      }),
    };

    fetchMock.mockResolvedValueOnce({
      ok: true,
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-type" ? "text/event-stream" : null,
      },
      body: {
        getReader: () => reader,
      },
    });

    render(<ChatInterface pdfId={pdfId} />);

    const input = screen.getByPlaceholderText(
      "Ask about the document...",
    ) as HTMLTextAreaElement;
    await userEvent.type(input, "What does BRCA1 do?");

    const sendButton = screen.getByTestId("chat-send") as HTMLButtonElement;
    await waitFor(() => expect(sendButton).not.toHaveAttribute("disabled"));
    await userEvent.click(sendButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    await waitFor(() =>
      expect(screen.getByText(/Assistant/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/BRCA1 repairs DNA/)).toBeInTheDocument();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/rag/sessions");
    const secondCall = fetchMock.mock.calls[1];
    expect(secondCall[0]).toBe("/api/rag/sessions/session-1/question");
    expect(secondCall[1]?.headers?.Accept).toBe("text/event-stream");
  });
});
