import { useState } from "react";
import { Box, Button, LinearProgress, Alert, Typography } from "@mui/material";

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
    .trim() || "Upload failed";

const PDFUpload = ({ onUploaded }: PDFUploadProps) => {
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

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
    setError(null);
    setSuccessMessage(null);

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
      setSuccessMessage("Upload complete");
      onUploaded?.({ pdfId, filename, viewerUrl });
    } catch (err) {
      const rawMessage = err instanceof Error ? err.message : "Upload failed";
      const plainMessage = sanitizeError(rawMessage);
      setError(plainMessage);
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

      {isUploading && <LinearProgress data-testid="upload-progress" />}
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
