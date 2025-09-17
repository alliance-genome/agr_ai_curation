import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  LinearProgress,
  Alert,
  Typography,
  Stack,
} from "@mui/material";

interface PDFUploadProps {
  onUploaded?: (info: {
    pdfId: string;
    filename: string;
    viewerUrl?: string;
  }) => void;
}

const sanitizeError = (value: string) =>
  value
    .replace(/<[^>]+>/g, "")
    .replace(/\s+/g, " ")
    .trim() || "Request failed";

const PDFUpload = ({ onUploaded }: PDFUploadProps) => {
  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [progressMessages, setProgressMessages] = useState<string[]>([]);
  const [progressPercent, setProgressPercent] = useState<number>(0);

  const pollingTimerRef = useRef<number | null>(null);
  type ProgressFlags = {
    extractionRecorded: boolean;
    extractionStarted: boolean;
    chunkingRecorded: boolean;
    embeddingStarted: boolean;
    embeddingComplete: boolean;
    waitingForDocument: boolean;
  };
  const progressFlagsRef = useRef<ProgressFlags>({
    extractionRecorded: false,
    extractionStarted: false,
    chunkingRecorded: false,
    embeddingStarted: false,
    embeddingComplete: false,
    waitingForDocument: false,
  });

  const clearPolling = useCallback(() => {
    if (pollingTimerRef.current !== null) {
      window.clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearPolling, [clearPolling]);

  const appendProgressMessage = useCallback((message: string) => {
    setProgressMessages((prev) =>
      prev.includes(message) ? prev : [...prev, message],
    );
  }, []);

  const TOTAL_STEPS = 5;

  const updateProgress = useCallback((step: number) => {
    const percent = Math.min(
      100,
      Math.max(0, Math.round((step / TOTAL_STEPS) * 100)),
    );
    setProgressPercent((prev) => (percent > prev ? percent : prev));
  }, []);

  const stopProcessing = useCallback(
    (message?: string, completed = true) => {
      clearPolling();
      setIsProcessing(false);
      if (completed) {
        updateProgress(TOTAL_STEPS);
      } else {
        setProgressPercent(0);
      }
      if (message) {
        appendProgressMessage(message);
      }
    },
    [appendProgressMessage, clearPolling, updateProgress],
  );

  const startStatusPolling = useCallback(
    (pdfId: string, filename: string) => {
      clearPolling();
      progressFlagsRef.current = {
        extractionRecorded: false,
        extractionStarted: false,
        chunkingRecorded: false,
        embeddingStarted: false,
        embeddingComplete: false,
        waitingForDocument: false,
      };
      setIsProcessing(true);

      const poll = async () => {
        try {
          const detailResp = await fetch(`/api/pdf-data/documents/${pdfId}`);
          if (!detailResp.ok) {
            if (!progressFlagsRef.current.waitingForDocument) {
              appendProgressMessage(
                "Waiting for the server to register the document…",
              );
              progressFlagsRef.current.waitingForDocument = true;
            }
            return;
          }
          const detail = await detailResp.json();
          if (progressFlagsRef.current.waitingForDocument) {
            progressFlagsRef.current.waitingForDocument = false;
          }

          const chunkCount = detail.chunk_count ?? 0;

          if (!progressFlagsRef.current.extractionStarted) {
            const strategy =
              typeof detail.extraction_method === "string"
                ? detail.extraction_method
                    .replace("UNSTRUCTURED_", "")
                    .replace(/_/g, " ")
                    .toLowerCase()
                : "configured";

            if (chunkCount > 0) {
              appendProgressMessage(
                "Document already processed. Reusing cached extraction and chunks.",
              );
              progressFlagsRef.current.extractionStarted = true;
              progressFlagsRef.current.extractionRecorded = true;
              progressFlagsRef.current.chunkingRecorded = true;
              updateProgress(4);
            } else {
              appendProgressMessage(
                `Running ${strategy} extraction (this may take a few minutes)…`,
              );
              progressFlagsRef.current.extractionStarted = true;
              updateProgress(2);
            }
          }

          if (
            !progressFlagsRef.current.extractionRecorded &&
            (detail.page_count ?? 0) > 0
          ) {
            appendProgressMessage(
              `Extraction finished – detected ${detail.page_count ?? "?"} pages.`,
            );
            progressFlagsRef.current.extractionRecorded = true;
            updateProgress(3);
          }

          if (!progressFlagsRef.current.chunkingRecorded && chunkCount > 0) {
            appendProgressMessage(
              `Chunking completed with ${detail.chunk_count} chunks.`,
            );
            progressFlagsRef.current.chunkingRecorded = true;
            updateProgress(4);
          }

          if (
            progressFlagsRef.current.chunkingRecorded &&
            !progressFlagsRef.current.embeddingStarted
          ) {
            appendProgressMessage(
              "Generating embeddings – this can take a moment for large PDFs…",
            );
            progressFlagsRef.current.embeddingStarted = true;
          }

          const embeddingsResp = await fetch(
            `/api/pdf-data/documents/${pdfId}/embeddings`,
          );
          if (!embeddingsResp.ok) {
            return;
          }
          const embeddings = await embeddingsResp.json();
          if (
            embeddings.length > 0 &&
            !progressFlagsRef.current.embeddingComplete
          ) {
            const summary = embeddings[0];
            const cost = summary.estimated_cost_usd
              ? ` (≈$${summary.estimated_cost_usd.toFixed(6)})`
              : "";
            appendProgressMessage(
              `Embeddings ready via ${summary.model_name}${cost}.`,
            );
            progressFlagsRef.current.embeddingComplete = true;
            updateProgress(TOTAL_STEPS);
            stopProcessing(`Processing complete for ${filename}.`);
          }
        } catch (pollError) {
          console.error("Failed to poll ingestion status", pollError);
        }
      };

      appendProgressMessage("Document received. Monitoring processing stages…");
      poll();
      pollingTimerRef.current = window.setInterval(poll, 2500);
    },
    [appendProgressMessage, clearPolling, stopProcessing, updateProgress],
  );

  const handleFileChange: React.ChangeEventHandler<HTMLInputElement> = async (
    event,
  ) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are supported");
      event.target.value = "";
      return;
    }

    setIsUploading(true);
    setIsProcessing(false);
    setError(null);
    setSuccessMessage(null);
    setProgressMessages(["Sending PDF to processing pipeline…"]);
    setProgressPercent(12);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch("/api/pdf/upload", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "Upload failed");
      }

      const payload = await response.json();
      const pdfId = payload.pdf_id as string;
      const filename = (payload.filename as string) ?? file.name;
      const viewerUrl = payload.viewer_url as string | undefined;
      const reused = Boolean(payload.reused);
      setSuccessMessage("Upload complete");
      appendProgressMessage("Transfer complete. Preparing extraction…");
      updateProgress(1);
      onUploaded?.({ pdfId, filename, viewerUrl });

      if (reused) {
        appendProgressMessage(
          "Document already processed. Reusing cached results from server.",
        );
        setIsProcessing(false);
        setProgressPercent(100);
        return;
      }

      startStatusPolling(pdfId, filename);
    } catch (err) {
      const rawMessage =
        err instanceof Error ? err.message : "Processing request failed";
      const plainMessage = sanitizeError(rawMessage);
      setError(plainMessage);
      appendProgressMessage(
        `Transfer failed: ${plainMessage}. Please retry or check backend logs.`,
      );
      stopProcessing(undefined, false);
    } finally {
      setIsUploading(false);
      event.target.value = "";
    }
  };

  return (
    <Box display="flex" flexDirection="column" gap={2}>
      <Typography variant="h6">Upload PDF</Typography>
      <Button
        variant="contained"
        component="label"
        color="primary"
        aria-label="Upload PDF"
      >
        Select PDF
        <input
          data-testid="pdf-input"
          type="file"
          hidden
          accept="application/pdf"
          onChange={handleFileChange}
        />
      </Button>

      {(isUploading || isProcessing) && (
        <Stack spacing={1}>
          <LinearProgress
            data-testid="upload-progress"
            variant="determinate"
            value={progressPercent}
          />
          <Typography variant="caption" color="text.secondary">
            {progressPercent}%
          </Typography>
        </Stack>
      )}

      {progressMessages.length > 0 && (
        <Box
          role="status"
          aria-live="polite"
          sx={{
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.05)"
                : "rgba(0,0,0,0.04)",
            border: (theme) => `1px solid ${theme.palette.divider}`,
            borderRadius: 1,
            px: 2,
            py: 1.5,
            fontFamily: '"Roboto Mono", monospace',
            maxHeight: 160,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 0.75,
          }}
        >
          {progressMessages.map((message) => (
            <Typography key={message} variant="body2" color="text.secondary">
              {message}
            </Typography>
          ))}
        </Box>
      )}
      {error && (
        <Alert severity="error" data-testid="upload-error">
          {error}
        </Alert>
      )}
      {successMessage && (
        <Alert severity="success" data-testid="upload-success">
          {successMessage}
        </Alert>
      )}
    </Box>
  );
};

export default PDFUpload;
