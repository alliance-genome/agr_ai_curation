import { useEffect, useState, useRef } from "react";
import {
  Box,
  TextField,
  Paper,
  Typography,
  Alert,
  CircularProgress,
  Card,
  IconButton,
  Tooltip,
  Chip,
} from "@mui/material";
import { styled } from "@mui/material/styles";
import { Send as SendIcon, ContentCopy as CopyIcon } from "@mui/icons-material";
import { format } from "date-fns";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: Date;
  citations?: { page?: number; section?: string }[];
}

interface ChatInterfaceProps {
  pdfId: string | null;
}

const ChatContainer = styled(Box)(({ theme }) => ({
  height: "100%",
  display: "flex",
  flexDirection: "column",
  padding: theme.spacing(2),
  backgroundColor: theme.palette.background.default,
}));

const MessagesContainer = styled(Box)(({ theme }) => ({
  flex: 1,
  overflowY: "auto",
  overflowX: "hidden",
  padding: theme.spacing(2),
  scrollBehavior: "smooth",
  display: "flex",
  flexDirection: "column",
  gap: theme.spacing(2),
  minHeight: 0,
  maxHeight: "100%",
  "&::-webkit-scrollbar": {
    width: "8px",
  },
  "&::-webkit-scrollbar-track": {
    backgroundColor: theme.palette.action.hover,
    borderRadius: "4px",
  },
  "&::-webkit-scrollbar-thumb": {
    backgroundColor: theme.palette.action.disabled,
    borderRadius: "4px",
    "&:hover": {
      backgroundColor: theme.palette.action.selected,
    },
  },
}));

const MessageCard = styled(Card, {
  shouldForwardProp: (prop) => prop !== "isUser",
})<{ isUser: boolean }>(({ theme, isUser }) => ({
  width: "fit-content",
  maxWidth: isUser ? "75%" : "90%",
  padding: theme.spacing(2),
  backgroundColor: isUser
    ? theme.palette.mode === "dark"
      ? theme.palette.grey[800]
      : theme.palette.grey[100]
    : theme.palette.mode === "dark"
      ? theme.palette.primary.dark
      : theme.palette.primary.light,
  alignSelf: isUser ? "flex-end" : "flex-start",
  borderRadius: theme.spacing(2),
  position: "relative",
  boxShadow: theme.shadows[1],
  overflow: "visible",
  wordBreak: "break-word",
  overflowWrap: "anywhere",
}));

const InputContainer = styled(Box)(({ theme }) => ({
  display: "flex",
  gap: theme.spacing(1),
  padding: theme.spacing(2),
  alignItems: "center",
  borderTop: `1px solid ${theme.palette.divider}`,
}));

const ChatInterface = ({ pdfId }: ChatInterfaceProps) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "end",
      });
    }, 50);
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

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
        timestamp: new Date(),
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

  const appendAssistantMessage = (id: string, delta: string) => {
    if (!delta) return;
    setMessages((prev) =>
      prev.map((message) =>
        message.id === id
          ? { ...message, text: `${message.text}${delta}` }
          : message,
      ),
    );
  };

  const removeAssistantMessage = (id: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== id));
  };

  const handleCopyMessage = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendQuestion();
    }
  };

  const sendQuestion = async () => {
    const trimmed = question.trim();
    if (!trimmed || loading) {
      return;
    }

    if (!pdfId) {
      setError("Upload a PDF before asking questions.");
      return;
    }

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      text: trimmed,
      timestamp: new Date(),
    };

    setQuestion("");
    setMessages((prev) => [...prev, userMessage]);
    scrollToBottom();
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
          body: JSON.stringify({ question: trimmed }),
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
    let endReceived = false;
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
              if (payload.type === "delta") {
                appendAssistantMessage(assistantId, payload.content ?? "");
                scrollToBottom();
              } else if (payload.type === "final") {
                updateAssistantMessage(assistantId, {
                  text: payload.answer ?? "",
                  citations: payload.citations ?? [],
                });
                scrollToBottom();
              } else if (payload.type === "end") {
                endReceived = true;
              } else if (payload.type === "error") {
                streamError = payload.message ?? "Stream error";
              }
              // Ignore "start" events
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

    if (!endReceived) {
      // It's OK if we didn't get an explicit "end" event as long as we got data
      console.warn("Stream ended without explicit end event");
    }
  };

  return (
    <ChatContainer>
      {!pdfId && (
        <Alert severity="info" data-testid="chat-no-pdf" sx={{ mb: 2 }}>
          Upload a PDF to start asking questions.
        </Alert>
      )}

      <Paper
        elevation={1}
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          borderRadius: 2,
        }}
      >
        <MessagesContainer data-testid="message-list">
          {messages.length === 0 && pdfId && (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "text.secondary",
              }}
            >
              <Typography variant="body1">
                Ask a question about the uploaded PDF...
              </Typography>
            </Box>
          )}

          {messages.map((message) => (
            <MessageCard key={message.id} isUser={message.role === "user"}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 1,
                  mb: 1,
                }}
              >
                <Typography variant="caption" sx={{ fontSize: "1.2rem" }}>
                  {message.role === "user" ? "" : "🤖"}
                </Typography>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 0.5 }}
                  >
                    {message.role === "user" ? "You" : "Assistant"} •{" "}
                    {format(message.timestamp, "HH:mm")}
                  </Typography>
                  <Typography
                    variant="body1"
                    sx={{
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      overflowWrap: "break-word",
                    }}
                  >
                    {message.text || "Thinking..."}
                  </Typography>
                  {message.citations && message.citations.length > 0 && (
                    <Box
                      sx={{
                        mt: 1,
                        display: "flex",
                        gap: 0.5,
                        flexWrap: "wrap",
                      }}
                    >
                      {message.citations.map((citation, idx) => (
                        <Chip
                          key={idx}
                          label={`Page ${citation.page ?? "?"}`}
                          size="small"
                          variant="outlined"
                        />
                      ))}
                    </Box>
                  )}
                </Box>
                <Tooltip title="Copy message">
                  <IconButton
                    size="small"
                    onClick={() => handleCopyMessage(message.text)}
                    sx={{ opacity: 0.6, "&:hover": { opacity: 1 } }}
                  >
                    <CopyIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </MessageCard>
          ))}

          {loading && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, ml: 2 }}>
              <CircularProgress size={20} />
              <Typography variant="caption" color="text.secondary">
                Assistant is thinking...
              </Typography>
            </Box>
          )}

          <div ref={messagesEndRef} />
        </MessagesContainer>

        {error && (
          <Alert severity="error" data-testid="chat-error" sx={{ m: 2 }}>
            {error}
          </Alert>
        )}

        <InputContainer>
          <TextField
            fullWidth
            multiline
            maxRows={4}
            minRows={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Ask about the document..."
            data-testid="chat-input"
            disabled={!pdfId}
            variant="outlined"
            sx={{
              "& .MuiOutlinedInput-root": {
                borderRadius: 2,
              },
            }}
          />
          <IconButton
            color="primary"
            onClick={sendQuestion}
            disabled={loading || !question.trim() || !pdfId}
            data-testid="chat-send"
            sx={{
              backgroundColor: "primary.main",
              color: "primary.contrastText",
              "&:hover": {
                backgroundColor: "primary.dark",
              },
              "&:disabled": {
                backgroundColor: "action.disabledBackground",
                color: "action.disabled",
              },
            }}
          >
            <SendIcon />
          </IconButton>
        </InputContainer>
      </Paper>
    </ChatContainer>
  );
};

export default ChatInterface;
