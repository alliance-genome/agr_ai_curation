import React, { useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";

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
  const [displayedContent, setDisplayedContent] = useState("");
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
      second: "2-digit",
    }).format(date);
  };

  const getModelBadge = () => {
    if (!modelInfo) return null;

    const providerColors = {
      openai: "bg-green-100 text-green-800",
      gemini: "bg-blue-100 text-blue-800",
    };

    const colorClass =
      providerColors[modelInfo.provider as keyof typeof providerColors] ||
      "bg-gray-100 text-gray-800";

    return (
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}
      >
        {modelInfo.model}
      </span>
    );
  };

  return (
    <div className={`message ${role} mb-4`}>
      <div
        className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}
      >
        <div className={`max-w-3xl ${role === "user" ? "order-2" : ""}`}>
          {/* Message header */}
          <div
            className={`flex items-center gap-2 mb-1 ${role === "user" ? "justify-end" : ""}`}
          >
            <span className="text-sm font-medium text-gray-700">
              {role === "user" ? "You" : "Assistant"}
            </span>
            {role === "assistant" && modelInfo && getModelBadge()}
            {timestamp && (
              <span className="text-xs text-gray-500">
                {formatTimestamp(timestamp)}
              </span>
            )}
            {isStreaming && (
              <span className="inline-flex items-center">
                <span className="animate-pulse text-xs text-gray-500">
                  Typing
                </span>
                <span className="flex space-x-1 ml-1">
                  <span
                    className="w-1 h-1 bg-gray-400 rounded-full animate-bounce"
                    style={{ animationDelay: "0ms" }}
                  ></span>
                  <span
                    className="w-1 h-1 bg-gray-400 rounded-full animate-bounce"
                    style={{ animationDelay: "150ms" }}
                  ></span>
                  <span
                    className="w-1 h-1 bg-gray-400 rounded-full animate-bounce"
                    style={{ animationDelay: "300ms" }}
                  ></span>
                </span>
              </span>
            )}
          </div>

          {/* Message content */}
          <div
            ref={contentRef}
            className={`
              px-4 py-2 rounded-lg
              ${
                role === "user"
                  ? "bg-blue-500 text-white"
                  : "bg-gray-100 text-gray-800 border border-gray-200"
              }
              ${isStreaming ? "min-h-[2rem]" : ""}
            `}
          >
            {role === "assistant" ? (
              <ReactMarkdown
                components={{
                  p: ({ children }) => (
                    <p className="mb-2 last:mb-0">{children}</p>
                  ),
                  code: ({ children, ...props }) => {
                    const match = /language-(\w+)/.exec(props.className || "");
                    const inline = !match;
                    return inline ? (
                      <code className="bg-gray-200 px-1 py-0.5 rounded text-sm">
                        {children}
                      </code>
                    ) : (
                      <code className="block bg-gray-800 text-gray-100 p-2 rounded text-sm overflow-x-auto">
                        {children}
                      </code>
                    );
                  },
                  ul: ({ children }) => (
                    <ul className="list-disc pl-4 mb-2">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal pl-4 mb-2">{children}</ol>
                  ),
                  li: ({ children }) => <li className="mb-1">{children}</li>,
                }}
              >
                {displayedContent || (isStreaming ? "..." : "")}
              </ReactMarkdown>
            ) : (
              <div className="whitespace-pre-wrap">{displayedContent}</div>
            )}

            {/* Cursor for streaming */}
            {isStreaming && role === "assistant" && (
              <span className="inline-block w-2 h-4 bg-gray-600 animate-pulse ml-0.5"></span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default StreamingMessage;
