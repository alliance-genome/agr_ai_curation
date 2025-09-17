import { useEffect, useState } from "react";
import {
  Box,
  Button,
  TextField,
  Paper,
  Typography,
  List,
  ListItem,
  ListItemText,
  Alert,
  CircularProgress,
} from "@mui/material";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations?: { page?: number; section?: string }[];
}

interface ChatInterfaceProps {
  pdfId: string | null;
}

const ChatInterface = ({ pdfId }: ChatInterfaceProps) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pdfId) {
      setMessages([]);
      setSessionId(null);
    }
  }, [pdfId]);

  const createAssistantMessage = () => {
    const assistantId = `assistant-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: assistantId,
        role: "assistant",
        text: "",
        citations: [],
      },
    ]);
    return assistantId;
  };

  const updateAssistantMessage = (id: string, update: Partial<Message>) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === id ? { ...message, ...update } : message,
      ),
    );
  };

  const removeAssistantMessage = (id: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== id));
  };

  const sendQuestion = async () => {
    if (!question.trim() || loading) {
      return;
    }

    if (!pdfId) {
      setError("Upload a PDF before asking questions.");
      return;
    }

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      text: question.trim(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setLoading(true);
    setError(null);

    let activeSessionId = sessionId;
    let assistantId: string | null = null;
    try {
      if (!activeSessionId) {
        const sessionResponse = await fetch("/api/rag/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pdf_id: pdfId }),
        });
        if (!sessionResponse.ok) {
          throw new Error("Failed to create session");
        }
        const sessionPayload = await sessionResponse.json();
        activeSessionId = sessionPayload.session_id;
        setSessionId(activeSessionId);
      }

      const questionResponse = await fetch(
        `/api/rag/sessions/${activeSessionId}/question`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({ question: question.trim() }),
        },
      );

      if (!questionResponse.ok) {
        throw new Error("Question request failed");
      }

      const contentType = questionResponse.headers.get("content-type") || "";
      assistantId = createAssistantMessage();

      if (contentType.includes("text/event-stream")) {
        await handleSseResponse(questionResponse, assistantId);
      } else {
        const payload = await questionResponse.json();
        updateAssistantMessage(assistantId, {
          text: payload.answer,
          citations: payload.citations,
        });
      }
    } catch (err) {
      if (assistantId) {
        removeAssistantMessage(assistantId);
      }
      const message =
        err instanceof Error ? err.message : "Unable to ask question";
      setError(message);
    } finally {
      setQuestion("");
      setLoading(false);
    }
  };

  const handleSseResponse = async (response: Response, assistantId: string) => {
    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("Streaming is not supported in this environment");
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let finalReceived = false;
    let streamError: string | null = null;

    const processBuffer = () => {
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (rawEvent) {
          const dataLine = rawEvent
            .split("\n")
            .find((line) => line.startsWith("data:"));
          if (dataLine) {
            try {
              const payload = JSON.parse(dataLine.slice(5).trim() || "{}");
              if (payload.type === "final") {
                updateAssistantMessage(assistantId, {
                  text: payload.answer ?? "",
                  citations: payload.citations ?? [],
                });
                finalReceived = true;
              } else if (payload.type === "error") {
                streamError = payload.message ?? "Stream error";
              }
            } catch (parseErr) {
              streamError = "Malformed streaming payload";
            }
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      processBuffer();
    }

    buffer += decoder.decode();
    processBuffer();

    if (streamError) {
      throw new Error(streamError);
    }

    if (!finalReceived) {
      throw new Error("Stream ended without final answer");
    }
  };

  return (
    <Paper
      elevation={1}
      sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}
    >
      <Typography variant="h6">Ask Questions</Typography>

      {!pdfId && (
        <Alert severity="info" data-testid="chat-no-pdf">
          Upload a PDF to start asking questions.
        </Alert>
      )}

      <List
        sx={{ flexGrow: 1, maxHeight: 320, overflowY: "auto" }}
        data-testid="message-list"
      >
        {messages.map((message) => (
          <ListItem key={message.id} alignItems="flex-start">
            <ListItemText
              primary={`${message.role === "user" ? "You" : "Assistant"}: ${message.text}`}
              secondary={
                message.citations && message.citations.length > 0 ? (
                  <Typography variant="caption">
                    Citations:{" "}
                    {message.citations.map((c) => c.page ?? "?").join(", ")}
                  </Typography>
                ) : undefined
              }
            />
          </ListItem>
        ))}
        {loading && (
          <ListItem>
            <CircularProgress size={20} />
          </ListItem>
        )}
      </List>

      {error && (
        <Alert severity="error" data-testid="chat-error">
          {error}
        </Alert>
      )}

      <Box display="flex" gap={2}>
        <TextField
          fullWidth
          multiline
          minRows={2}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about the document..."
          data-testid="chat-input"
          disabled={!pdfId}
        />
        <Button
          variant="contained"
          onClick={sendQuestion}
          disabled={loading || !question.trim() || !pdfId}
          data-testid="chat-send"
        >
          Send
        </Button>
      </Box>
    </Paper>
  );
};

export default ChatInterface;
