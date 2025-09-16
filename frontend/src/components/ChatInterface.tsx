import { useState } from "react";
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
  pdfId: string;
}

const ChatInterface = ({ pdfId }: ChatInterfaceProps) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendQuestion = async () => {
    if (!question.trim() || loading) {
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
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: question.trim() }),
        },
      );

      if (!questionResponse.ok) {
        throw new Error("Question request failed");
      }

      const payload = await questionResponse.json();
      const assistantMessage: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        text: payload.answer,
        citations: payload.citations,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unable to ask question";
      setError(message);
    } finally {
      setQuestion("");
      setLoading(false);
    }
  };

  return (
    <Paper
      elevation={1}
      sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}
    >
      <Typography variant="h6">Ask Questions</Typography>

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
        />
        <Button
          variant="contained"
          onClick={sendQuestion}
          disabled={loading || !question.trim()}
          data-testid="chat-send"
        >
          Send
        </Button>
      </Box>
    </Paper>
  );
};

export default ChatInterface;
