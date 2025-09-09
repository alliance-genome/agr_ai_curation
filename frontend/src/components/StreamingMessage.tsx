import React, { useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import {
  Box,
  Typography,
  Avatar,
  Chip,
  Paper,
  CircularProgress,
} from "@mui/material";
import { Person, SmartToy } from "@mui/icons-material";

interface StreamingMessageProps {
  content: string;
  isStreaming: boolean;
  role: "user" | "assistant";
  timestamp?: Date;
  modelInfo?: {
    provider: string;
    model: string;
  };
}

const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isStreaming,
  role,
  timestamp,
  modelInfo,
}) => {
  const [displayedContent, setDisplayedContent] = useState(
    isStreaming ? "" : content,
  );
  const contentRef = useRef<HTMLDivElement>(null);
  const streamIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (isStreaming && content !== displayedContent) {
      // Clear any existing interval
      if (streamIntervalRef.current) {
        clearInterval(streamIntervalRef.current);
      }

      // Simulate smooth text appearance for streaming
      const contentLength = content.length;
      const displayLength = displayedContent.length;

      if (contentLength > displayLength) {
        const remaining = content.substring(displayLength);
        const chunkSize = Math.min(3, remaining.length); // Display 3 chars at a time

        streamIntervalRef.current = setInterval(() => {
          setDisplayedContent((prev) => {
            const newLength = Math.min(prev.length + chunkSize, content.length);
            const newContent = content.substring(0, newLength);

            if (newLength >= content.length) {
              if (streamIntervalRef.current) {
                clearInterval(streamIntervalRef.current);
                streamIntervalRef.current = null;
              }
            }

            return newContent;
          });
        }, 10); // Update every 10ms for smooth appearance
      }
    } else if (!isStreaming) {
      // When streaming stops, show full content immediately
      setDisplayedContent(content);
      if (streamIntervalRef.current) {
        clearInterval(streamIntervalRef.current);
        streamIntervalRef.current = null;
      }
    }

    return () => {
      if (streamIntervalRef.current) {
        clearInterval(streamIntervalRef.current);
      }
    };
  }, [content, isStreaming, displayedContent]);

  // Auto-scroll to bottom when content updates
  useEffect(() => {
    if (contentRef.current && isStreaming) {
      contentRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [displayedContent, isStreaming]);

  const formatTimestamp = (date?: Date): string => {
    if (!date) return "";
    return new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  };

  const getModelBadge = () => {
    if (!modelInfo) return null;

    const providerColors = {
      openai: "success",
      gemini: "info",
    } as const;

    const color =
      providerColors[modelInfo.provider as keyof typeof providerColors] ||
      "default";

    return (
      <Chip
        label={modelInfo.model}
        size="small"
        color={color}
        variant="outlined"
        sx={{ height: 20, fontSize: "0.75rem" }}
      />
    );
  };

  const isUser = role === "user";

  return (
    <Box
      sx={{
        display: "flex",
        gap: 2,
        mb: 3,
        flexDirection: isUser ? "row-reverse" : "row",
      }}
    >
      <Avatar
        sx={{
          bgcolor: isUser ? "primary.main" : "grey.200",
          width: 36,
          height: 36,
          flexShrink: 0,
        }}
      >
        {isUser ? <Person fontSize="small" /> : <SmartToy fontSize="small" />}
      </Avatar>

      <Box sx={{ maxWidth: "70%", minWidth: 0 }}>
        {/* Message header */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            mb: 0.5,
            flexDirection: isUser ? "row-reverse" : "row",
          }}
        >
          <Typography variant="subtitle2" color="text.secondary">
            {isUser ? "You" : "Assistant"}
          </Typography>
          {!isUser && modelInfo && getModelBadge()}
          {timestamp && (
            <Typography variant="caption" color="text.disabled">
              {formatTimestamp(timestamp)}
            </Typography>
          )}
          {isStreaming && !isUser && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <CircularProgress size={12} thickness={5} />
              <Typography variant="caption" color="text.disabled">
                Responding
              </Typography>
            </Box>
          )}
        </Box>

        {/* Message content */}
        <Paper
          ref={contentRef}
          elevation={0}
          sx={{
            p: 2,
            bgcolor: isUser ? "primary.main" : "#f5f5f5",
            color: isUser ? "primary.contrastText" : "#000000",
            borderRadius: 2,
            borderTopLeftRadius: isUser ? 16 : 4,
            borderTopRightRadius: isUser ? 4 : 16,
            position: "relative",
            minHeight: isStreaming ? "2rem" : "auto",
            border: 1,
            borderColor: isUser ? "primary.dark" : "divider",
          }}
        >
          {!isUser ? (
            <ReactMarkdown
              components={{
                p: ({ children }) => (
                  <Typography
                    variant="body2"
                    paragraph
                    sx={{ mb: 1.5, "&:last-child": { mb: 0 } }}
                  >
                    {children}
                  </Typography>
                ),
                code: ({ children, className }) => {
                  const match = /language-(\w+)/.exec(className || "");
                  const inline = !match;
                  return inline ? (
                    <Box
                      component="code"
                      sx={{
                        px: 0.75,
                        py: 0.25,
                        borderRadius: 0.5,
                        bgcolor: "grey.200",
                        color: "text.primary",
                        fontSize: "0.875rem",
                        fontFamily: "monospace",
                      }}
                    >
                      {children}
                    </Box>
                  ) : (
                    <Box
                      component="pre"
                      sx={{
                        p: 2,
                        borderRadius: 1,
                        bgcolor: "grey.900",
                        color: "grey.100",
                        overflow: "auto",
                        fontSize: "0.875rem",
                        fontFamily: "monospace",
                        my: 1.5,
                      }}
                    >
                      <code>{children}</code>
                    </Box>
                  );
                },
                ul: ({ children }) => (
                  <Box component="ul" sx={{ pl: 2, my: 1 }}>
                    {children}
                  </Box>
                ),
                ol: ({ children }) => (
                  <Box component="ol" sx={{ pl: 2, my: 1 }}>
                    {children}
                  </Box>
                ),
                li: ({ children }) => (
                  <Typography component="li" variant="body2" sx={{ mb: 0.5 }}>
                    {children}
                  </Typography>
                ),
                strong: ({ children }) => (
                  <Box component="strong" sx={{ fontWeight: 600 }}>
                    {children}
                  </Box>
                ),
                em: ({ children }) => (
                  <Box component="em" sx={{ fontStyle: "italic" }}>
                    {children}
                  </Box>
                ),
              }}
            >
              {displayedContent || (isStreaming ? "..." : "")}
            </ReactMarkdown>
          ) : (
            <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
              {displayedContent}
            </Typography>
          )}

          {/* Cursor for streaming */}
          {isStreaming && !isUser && (
            <Box
              component="span"
              sx={{
                display: "inline-block",
                width: 2,
                height: 16,
                bgcolor: "text.primary",
                ml: 0.5,
                animation: "blink 1s infinite",
                "@keyframes blink": {
                  "0%": { opacity: 1 },
                  "50%": { opacity: 0 },
                  "100%": { opacity: 1 },
                },
              }}
            />
          )}
        </Paper>
      </Box>
    </Box>
  );
};

export default StreamingMessage;
