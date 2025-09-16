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
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        answer: "BRCA1 repairs DNA.",
        citations: [{ page: 3 }],
        metadata: {},
      }),
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
      expect(screen.getByText(/Assistant:/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/BRCA1 repairs DNA/)).toBeInTheDocument();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/rag/sessions");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/rag/sessions/session-1/question",
    );
  });
});
