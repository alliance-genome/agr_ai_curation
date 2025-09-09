import { useState, useRef, useEffect } from "react";
import {
  Box,
  Paper,
  TextField,
  IconButton,
  Button,
  Avatar,
  Typography,
  CircularProgress,
  Alert,
} from "@mui/material";
import { Send, Clear, SmartToy } from "@mui/icons-material";
import axios from "axios";
import { PdfTextData } from "../types/pdf";
import ModelSelector from "./ModelSelector";
import StreamingMessage from "./StreamingMessage";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  modelInfo?: {
    provider: string;
    model: string;
  };
}

interface ChatInterfaceProps {
  pdfTextData?: PdfTextData | null;
}

function ChatInterface({}: ChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState("openai");
  const [selectedModel, setSelectedModel] = useState("gpt-4o");
  const [streamingEnabled, setStreamingEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);

    const chatHistory = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    try {
      if (streamingEnabled) {
        // Streaming response using Server-Sent Events
        const assistantMessage: Message = {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
          modelInfo: {
            provider: selectedProvider,
            model: selectedModel,
          },
        };

        setMessages((prev) => [...prev, assistantMessage]);

        const response = await fetch("/api/chat/stream", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: input,
            history: chatHistory,
            session_id: sessionId,
            provider: selectedProvider,
            model: selectedModel,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        if (reader) {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (line.startsWith("data: ")) {
                const data = line.slice(6);
                if (data.trim()) {
                  try {
                    const parsed = JSON.parse(data);

                    if (parsed.error) {
                      throw new Error(parsed.error);
                    }

                    if (parsed.session_id && !sessionId) {
                      setSessionId(parsed.session_id);
                    }

                    if (parsed.delta) {
                      setMessages((prev) => {
                        const newMessages = [...prev];
                        const lastMessage = newMessages[newMessages.length - 1];
                        if (lastMessage && lastMessage.role === "assistant") {
                          lastMessage.content += parsed.delta;
                        }
                        return newMessages;
                      });
                    }

                    if (parsed.is_complete) {
                      setMessages((prev) => {
                        const newMessages = [...prev];
                        const lastMessage = newMessages[newMessages.length - 1];
                        if (lastMessage && lastMessage.role === "assistant") {
                          lastMessage.isStreaming = false;
                        }
                        return newMessages;
                      });
                    }
                  } catch (e) {
                    console.error("Error parsing SSE data:", e);
                  }
                }
              }
            }
          }
        }
      } else {
        // Non-streaming response
        const response = await axios.post("/api/chat/", {
          message: input,
          history: chatHistory,
          session_id: sessionId,
          provider: selectedProvider,
          model: selectedModel,
        });

        if (response.data.session_id && !sessionId) {
          setSessionId(response.data.session_id);
        }

        const assistantMessage: Message = {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content:
            response.data.response ||
            "I understand. How can I help you further?",
          timestamp: new Date(),
          isStreaming: false,
          modelInfo: {
            provider: response.data.provider || selectedProvider,
            model: response.data.model || selectedModel,
          },
        };

        setMessages((prev) => [...prev, assistantMessage]);
      }
    } catch (error: any) {
      console.error("Chat error:", error);
      setError(
        error.message || "An error occurred while processing your request.",
      );

      // Remove streaming message if it was added
      if (streamingEnabled) {
        setMessages((prev) => {
          const newMessages = [...prev];
          const lastMessage = newMessages[newMessages.length - 1];
          if (
            lastMessage &&
            lastMessage.role === "assistant" &&
            lastMessage.isStreaming
          ) {
            newMessages.pop();
          }
          return newMessages;
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    setMessages([]);
    setSessionId(null);
    setError(null);
  };

  const handleModelChange = (provider: string, model: string) => {
    setSelectedProvider(provider);
    setSelectedModel(model);
  };

  // Cleanup event source on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return (
    <Paper
      sx={{ height: "100%", display: "flex", flexDirection: "column", p: 2 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", mb: 2, gap: 2 }}>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          AI Assistant
        </Typography>
        <ModelSelector onModelChange={handleModelChange} disabled={loading} />
        <Button
          variant="outlined"
          size="small"
          onClick={() => setStreamingEnabled(!streamingEnabled)}
          color={streamingEnabled ? "primary" : "inherit"}
        >
          {streamingEnabled ? "Streaming ON" : "Streaming OFF"}
        </Button>
        <Button
          variant="outlined"
          size="small"
          startIcon={<Clear />}
          onClick={handleClear}
          disabled={messages.length === 0}
        >
          Clear
        </Button>
      </Box>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Box sx={{ flexGrow: 1, overflow: "auto", mb: 2, px: 2 }}>
        {messages.map((message) => (
          <StreamingMessage
            key={message.id}
            content={message.content}
            isStreaming={message.isStreaming || false}
            role={message.role}
            timestamp={message.timestamp}
            modelInfo={message.modelInfo}
          />
        ))}
        {loading && messages[messages.length - 1]?.role !== "assistant" && (
          <Box sx={{ display: "flex", gap: 1, alignItems: "center", mt: 2 }}>
            <Avatar sx={{ bgcolor: "grey.600", width: 32, height: 32 }}>
              <SmartToy />
            </Avatar>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">
              Thinking...
            </Typography>
          </Box>
        )}
        <div ref={messagesEndRef} />
      </Box>

      <Box sx={{ display: "flex", gap: 1 }}>
        <TextField
          fullWidth
          multiline
          maxRows={4}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="Type your message..."
          disabled={loading}
          sx={{
            "& .MuiOutlinedInput-root": {
              borderRadius: 2,
            },
          }}
        />
        <IconButton
          color="primary"
          onClick={handleSend}
          disabled={!input.trim() || loading}
          sx={{
            bgcolor: "primary.main",
            color: "primary.contrastText",
            "&:hover": {
              bgcolor: "primary.dark",
            },
            "&:disabled": {
              bgcolor: "action.disabledBackground",
            },
          }}
        >
          <Send />
        </IconButton>
      </Box>
    </Paper>
  );
}

export default ChatInterface;
