import { useState, useRef, useEffect } from "react";
import {
  Box,
  Paper,
  TextField,
  IconButton,
  Avatar,
  Typography,
  CircularProgress,
  Alert,
  ToggleButton,
  ToggleButtonGroup,
  Divider,
  Tooltip,
  Chip,
  List,
  ListItem,
  ListItemText,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from "@mui/material";
import {
  Send,
  Clear,
  ExpandMore,
  Science,
  Biotech,
  Description,
} from "@mui/icons-material";
import axios from "axios";
import { PdfTextData } from "../types/pdf";
import StreamingMessage from "./StreamingMessage";

interface Entity {
  text: string;
  type: string;
  normalized_form?: string;
  database_id?: string;
  confidence: number;
  context?: string;
}

interface Annotation {
  text: string;
  start_position?: number;
  end_position?: number;
  color: string;
  category: string;
  note?: string;
  confidence: number;
}

interface BioCurationOutput {
  response: string;
  entities: Entity[];
  annotations: Annotation[];
  confidence: number;
  requires_review: boolean;
  curation_category?: string;
  key_findings: string[];
  references: string[];
  processing_time?: number;
  model_used?: string;
}

interface AgentMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  modelInfo?: {
    provider: string;
    model: string;
  };
  curationOutput?: BioCurationOutput;
}

interface AgentInterfaceProps {
  pdfTextData?: PdfTextData | null;
  selectedText?: string;
}

function AgentInterface({ pdfTextData, selectedText }: AgentInterfaceProps) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const selectedProvider = "openai";
  const selectedModel = "gpt-4o";
  const [includeEntities, setIncludeEntities] = useState(true);
  const [includeAnnotations, setIncludeAnnotations] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [messageHistory, setMessageHistory] = useState<any[]>([]); // Store PydanticAI message history
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: AgentMessage = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);

    // Prepare context if we have PDF data or selected text
    const context = {
      document_text: pdfTextData?.fullText || undefined,
      selected_text: selectedText || undefined,
    };

    // Map UI provider names to backend provider names
    const backendProvider = "openai";

    const requestData = {
      message: input,
      context: pdfTextData || selectedText ? context : undefined,
      session_id: sessionId,
      stream: true,
      include_entities: includeEntities,
      include_annotations: includeAnnotations,
      model_preference: `${backendProvider}:${selectedModel}`,
      message_history: messageHistory.length > 0 ? messageHistory : undefined,
    };
    const streamResponses = true;
    let currentOutput: Partial<BioCurationOutput> | null = null;

    const ensureOutput = () => {
      if (!currentOutput) {
        currentOutput = {
          response: "",
          entities: [],
          annotations: [],
          key_findings: [],
          references: [],
          confidence: 0,
          requires_review: false,
        };
      }
      return currentOutput;
    };

    try {
      if (streamResponses) {
        // Streaming response using Server-Sent Events
        const assistantMessage: AgentMessage = {
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

        const outputRef = ensureOutput();

        const response = await fetch("/api/agents/biocurate/stream", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(requestData),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let currentOutput: Partial<BioCurationOutput> = {
          entities: [],
          annotations: [],
          key_findings: [],
          references: [],
        };

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

                    // Handle different types of streaming updates
                    if (parsed.type === "text") {
                      // Full text update (replaces content)
                      setMessages((prev) => {
                        const newMessages = [...prev];
                        const lastMessage = newMessages[newMessages.length - 1];
                        if (lastMessage && lastMessage.role === "assistant") {
                          outputRef.response = parsed.content;
                          newMessages[newMessages.length - 1] = {
                            ...lastMessage,
                            content: parsed.content,
                          };
                        }
                        return newMessages;
                      });
                    } else if (parsed.type === "text_delta") {
                      // Delta text update (appends content)
                      setMessages((prev) => {
                        const newMessages = [...prev];
                        const lastMessage = newMessages[newMessages.length - 1];
                        if (lastMessage && lastMessage.role === "assistant") {
                          outputRef.response =
                            (outputRef.response || "") + parsed.content;
                          newMessages[newMessages.length - 1] = {
                            ...lastMessage,
                            content: lastMessage.content + parsed.content,
                          };
                        }
                        return newMessages;
                      });
                    } else if (parsed.type === "entity") {
                      if (parsed.metadata) {
                        ensureOutput();
                        currentOutput?.entities?.push(parsed.metadata);
                      }
                    } else if (parsed.type === "annotation") {
                      if (parsed.metadata) {
                        ensureOutput();
                        currentOutput?.annotations?.push(parsed.metadata);
                      }
                    } else if (parsed.type === "metadata") {
                      currentOutput = {
                        ...ensureOutput(),
                        ...parsed.metadata,
                      };
                    } else if (parsed.type === "history") {
                      // Message history update - store for next request
                      if (parsed.metadata?.messages) {
                        setMessageHistory(parsed.metadata.messages);
                      }
                    } else if (parsed.type === "event") {
                      // General event from the agent
                      console.log(
                        "Agent event:",
                        parsed.content,
                        parsed.metadata,
                      );
                    } else if (parsed.type === "complete") {
                      setMessages((prev) => {
                        const newMessages = [...prev];
                        const lastMessage = newMessages[newMessages.length - 1];
                        if (lastMessage && lastMessage.role === "assistant") {
                          newMessages[newMessages.length - 1] = {
                            ...lastMessage,
                            isStreaming: false,
                            curationOutput: ensureOutput() as BioCurationOutput,
                          };
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
        const response = await axios.post("/api/agents/biocurate", requestData);

        if (response.data.session_id && !sessionId) {
          setSessionId(response.data.session_id);
        }

        // Update message history for next request
        if (response.data.message_history) {
          setMessageHistory(response.data.message_history);
        }

        const assistantMessage: AgentMessage = {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: response.data.output.response,
          timestamp: new Date(),
          isStreaming: false,
          modelInfo: {
            provider: selectedProvider,
            model: selectedModel,
          },
          curationOutput: response.data.output,
        };

        setMessages((prev) => [...prev, assistantMessage]);
      }
    } catch (error: any) {
      console.error("Agent error:", error);
      setError(
        error.message || "An error occurred while processing your request.",
      );

      // Remove streaming message if it was added
      if (streamResponses) {
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
      if (streamResponses) {
        setMessages((prev) => {
          if (prev.length === 0) return prev;
          const newMessages = [...prev];
          const lastMessage = newMessages[newMessages.length - 1];
          if (!lastMessage || lastMessage.role !== "assistant") {
            return prev;
          }
          if (!lastMessage.isStreaming) {
            return prev;
          }

          const finalOutput = ensureOutput();
          newMessages[newMessages.length - 1] = {
            ...lastMessage,
            isStreaming: false,
            curationOutput:
              lastMessage.curationOutput || (finalOutput as BioCurationOutput),
          };
          return newMessages;
        });
      }
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
    setMessageHistory([]);
  };

  const renderEntityChip = (entity: Entity) => (
    <Chip
      key={`${entity.text}-${entity.type}`}
      label={`${entity.text} (${entity.type})`}
      size="small"
      color={entity.confidence > 0.8 ? "success" : "default"}
      variant={entity.confidence > 0.8 ? "filled" : "outlined"}
      sx={{ m: 0.5 }}
      title={`Confidence: ${(entity.confidence * 100).toFixed(0)}%${
        entity.database_id ? ` | ID: ${entity.database_id}` : ""
      }`}
    />
  );

  const renderAnnotation = (annotation: Annotation) => (
    <ListItem key={`${annotation.text}-${annotation.category}`} dense>
      <ListItemText
        primary={
          <Typography variant="body2" component="span">
            <Chip
              label={annotation.color}
              size="small"
              sx={{
                bgcolor: annotation.color.toLowerCase(),
                color: "white",
                mr: 1,
                height: 20,
              }}
            />
            {annotation.text.substring(0, 100)}
            {annotation.text.length > 100 && "..."}
          </Typography>
        }
        secondary={`${annotation.category} (${(
          annotation.confidence * 100
        ).toFixed(0)}% confidence)`}
      />
    </ListItem>
  );

  return (
    <Paper
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        p: 0,
        overflow: "hidden",
      }}
      elevation={0}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          p: 2,
          borderBottom: 1,
          borderColor: "divider",
          bgcolor: "background.default",
        }}
      >
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 1, flexGrow: 1 }}
        >
          <Biotech color="primary" />
          <Typography variant="h6">BioCuration Agent</Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Chip
            label="Model: OpenAI · GPT-4o"
            color="primary"
            variant="outlined"
            size="small"
            sx={{ fontWeight: 500 }}
          />
          <Divider orientation="vertical" flexItem sx={{ mx: 1 }} />
          <ToggleButtonGroup
            size="small"
            value={[
              includeEntities && "entities",
              includeAnnotations && "annotations",
            ].filter(Boolean)}
            onChange={(_, newValues) => {
              setIncludeEntities(newValues.includes("entities"));
              setIncludeAnnotations(newValues.includes("annotations"));
            }}
          >
            <ToggleButton value="entities" disabled={loading}>
              <Tooltip title="Extract entities">
                <Science />
              </Tooltip>
            </ToggleButton>
            <ToggleButton value="annotations" disabled={loading}>
              <Tooltip title="Suggest annotations">
                <Description />
              </Tooltip>
            </ToggleButton>
          </ToggleButtonGroup>
          <Tooltip title="Clear conversation">
            <IconButton
              onClick={handleClear}
              disabled={messages.length === 0}
              size="small"
              sx={{
                ml: 1,
                color: "text.secondary",
                "&:hover": {
                  color: "error.main",
                  bgcolor: (theme) => `${theme.palette.error.main}10`,
                },
              }}
            >
              <Clear />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      {error && (
        <Alert
          severity="error"
          onClose={() => setError(null)}
          sx={{ mx: 2, mt: 2 }}
        >
          {error}
        </Alert>
      )}

      <Box
        sx={{
          flexGrow: 1,
          overflow: "auto",
          p: 3,
          bgcolor: "background.default",
        }}
      >
        {messages.length === 0 && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "text.secondary",
            }}
          >
            <Biotech sx={{ fontSize: 48, mb: 2, opacity: 0.5 }} />
            <Typography variant="h6" gutterBottom>
              Start a biocuration session
            </Typography>
            <Typography variant="body2" color="text.secondary">
              I can help extract entities, suggest annotations, and analyze
              biological literature
            </Typography>
          </Box>
        )}
        {messages.map((message) => (
          <Box key={message.id}>
            <StreamingMessage
              content={message.content}
              isStreaming={message.isStreaming || false}
              role={message.role}
              timestamp={message.timestamp}
              modelInfo={message.modelInfo}
            />
            {message.curationOutput && (
              <Box sx={{ ml: 7, mt: 2, mb: 3 }}>
                {message.curationOutput.entities.length > 0 && (
                  <Accordion defaultExpanded>
                    <AccordionSummary expandIcon={<ExpandMore />}>
                      <Typography variant="subtitle2">
                        Extracted Entities (
                        {message.curationOutput.entities.length})
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails>
                      <Box sx={{ display: "flex", flexWrap: "wrap" }}>
                        {message.curationOutput.entities.map(renderEntityChip)}
                      </Box>
                    </AccordionDetails>
                  </Accordion>
                )}
                {message.curationOutput.annotations.length > 0 && (
                  <Accordion>
                    <AccordionSummary expandIcon={<ExpandMore />}>
                      <Typography variant="subtitle2">
                        Suggested Annotations (
                        {message.curationOutput.annotations.length})
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails>
                      <List dense>
                        {message.curationOutput.annotations.map(
                          renderAnnotation,
                        )}
                      </List>
                    </AccordionDetails>
                  </Accordion>
                )}
                {message.curationOutput.key_findings.length > 0 && (
                  <Accordion>
                    <AccordionSummary expandIcon={<ExpandMore />}>
                      <Typography variant="subtitle2">Key Findings</Typography>
                    </AccordionSummary>
                    <AccordionDetails>
                      <List dense>
                        {message.curationOutput.key_findings.map((finding) => (
                          <ListItem key={finding}>
                            <ListItemText primary={finding} />
                          </ListItem>
                        ))}
                      </List>
                    </AccordionDetails>
                  </Accordion>
                )}
              </Box>
            )}
          </Box>
        ))}
        {loading && messages[messages.length - 1]?.role !== "assistant" && (
          <Box
            sx={{
              display: "flex",
              gap: 1.5,
              alignItems: "center",
              mt: 3,
              ml: 1,
            }}
          >
            <Avatar
              sx={{
                bgcolor: "primary.light",
                width: 36,
                height: 36,
              }}
            >
              <Biotech fontSize="small" />
            </Avatar>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <CircularProgress size={16} thickness={5} />
              <Typography variant="body2" color="text.secondary">
                Processing biocuration request...
              </Typography>
            </Box>
          </Box>
        )}
        <div ref={messagesEndRef} />
      </Box>

      <Box
        sx={{
          p: 2,
          borderTop: 1,
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Box sx={{ display: "flex", gap: 1.5 }}>
          <TextField
            fullWidth
            multiline
            maxRows={4}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Ask about genes, proteins, diseases, or request curation help..."
            disabled={loading}
            variant="outlined"
            size="small"
            sx={{
              "& .MuiOutlinedInput-root": {
                borderRadius: 2,
                bgcolor: "background.default",
                "&:hover": {
                  "& .MuiOutlinedInput-notchedOutline": {
                    borderColor: "primary.main",
                  },
                },
                "&.Mui-focused": {
                  "& .MuiOutlinedInput-notchedOutline": {
                    borderColor: "primary.main",
                  },
                },
              },
            }}
          />
          <Tooltip title="Send message">
            <span>
              <IconButton
                color="primary"
                onClick={handleSend}
                disabled={!input.trim() || loading}
                sx={{
                  bgcolor: "primary.main",
                  color: "primary.contrastText",
                  width: 44,
                  height: 44,
                  "&:hover": {
                    bgcolor: "primary.dark",
                    transform: "scale(1.05)",
                  },
                  "&:disabled": {
                    bgcolor: "action.disabledBackground",
                    color: "action.disabled",
                  },
                  transition: "all 0.2s",
                }}
              >
                {loading ? (
                  <CircularProgress size={20} color="inherit" />
                ) : (
                  <Send />
                )}
              </IconButton>
            </span>
          </Tooltip>
        </Box>
      </Box>
    </Paper>
  );
}

export default AgentInterface;
