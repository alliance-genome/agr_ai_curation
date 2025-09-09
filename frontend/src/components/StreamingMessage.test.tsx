import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import StreamingMessage from "./StreamingMessage";

describe("StreamingMessage", () => {
  it("renders user message correctly", async () => {
    render(
      <StreamingMessage
        content="Hello, how are you?"
        role="user"
        isStreaming={false}
        timestamp={new Date("2024-01-01T12:00:00")}
      />,
    );

    expect(screen.getByText("Hello, how are you?")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
  });

  it("renders assistant message correctly", async () => {
    render(
      <StreamingMessage
        content="I am doing well, thank you!"
        role="assistant"
        isStreaming={false}
        modelInfo={{
          provider: "openai",
          model: "gpt-4o",
        }}
      />,
    );

    // Wait for content to be displayed
    await waitFor(() => {
      expect(
        screen.getByText("I am doing well, thank you!"),
      ).toBeInTheDocument();
    });

    expect(screen.getByText("Assistant")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("shows streaming indicator when message is streaming", async () => {
    render(
      <StreamingMessage
        content="Thinking..."
        role="assistant"
        isStreaming={true}
      />,
    );

    // For streaming messages, content appears progressively
    // Check for streaming indicator (CircularProgress)
    const progressIndicator = document.querySelector(
      ".MuiCircularProgress-root",
    );
    expect(progressIndicator).toBeInTheDocument();
  });

  it("renders markdown content properly", async () => {
    render(
      <StreamingMessage
        content="# Hello\n\nThis is **bold** text and `code`."
        role="assistant"
        isStreaming={false}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Hello",
    );
    expect(screen.getByText("bold")).toBeInTheDocument();
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("renders code blocks with syntax highlighting", async () => {
    const codeContent = '```python\ndef hello():\n    print("Hello")\n```';
    render(
      <StreamingMessage
        content={codeContent}
        role="assistant"
        isStreaming={false}
      />,
    );

    // Wait for markdown to be rendered
    await waitFor(() => {
      // Look for the code content within the component
      const codeElements = screen.getAllByText((content, element) => {
        return element?.tagName === "CODE" && content.includes("def hello()");
      });
      expect(codeElements.length).toBeGreaterThan(0);
    });
  });

  it("applies correct styling for user messages", () => {
    render(
      <StreamingMessage
        content="Hello, how are you?"
        role="user"
        isStreaming={false}
      />,
    );

    const messageContainer = screen
      .getByText("Hello, how are you?")
      .closest(".MuiPaper-root");
    expect(messageContainer).toHaveStyle({
      backgroundColor: expect.stringMatching(/primary/i),
    });
  });

  it("applies correct styling for assistant messages", async () => {
    render(
      <StreamingMessage
        content="Hello, how are you?"
        role="assistant"
        isStreaming={false}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Hello, how are you?")).toBeInTheDocument();
    });

    const messageContainer = screen
      .getByText("Hello, how are you?")
      .closest(".MuiPaper-root");
    expect(messageContainer).toHaveStyle({
      backgroundColor: "#f5f5f5",
    });
  });

  it("displays timestamp in correct format", () => {
    render(
      <StreamingMessage
        content="Hello"
        role="user"
        isStreaming={false}
        timestamp={new Date("2024-01-01T12:00:00")}
      />,
    );

    // The timestamp should be formatted
    expect(screen.getByText(/12:00 PM/i)).toBeInTheDocument();
  });
});
