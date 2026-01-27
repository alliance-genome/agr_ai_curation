import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemButton,
  Typography,
  CircularProgress,
  Alert,
  Box,
} from '@mui/material';
import {
  PictureAsPdf,
  Code,
  DataObject,
  Download,
} from '@mui/icons-material';

interface DocumentDownloadDialogProps {
  open: boolean;
  documentId: string | null;
  onClose: () => void;
}

interface DownloadableFile {
  type: 'pdf' | 'docling_json' | 'processed_json';
  label: string;
  description: string;
  icon: React.ReactNode;
  available: boolean;
  size?: number;
}

const DocumentDownloadDialog: React.FC<DocumentDownloadDialogProps> = ({
  open,
  documentId,
  onClose,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadableFiles, setDownloadableFiles] = useState<DownloadableFile[]>([]);

  useEffect(() => {
    if (open && documentId) {
      fetchDownloadInfo();
    }
  }, [open, documentId]);

  const fetchDownloadInfo = async () => {
    if (!documentId) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/weaviate/documents/${documentId}/download-info`);

      if (!response.ok) {
        throw new Error('Failed to fetch download information');
      }

      const data = await response.json();

      const files: DownloadableFile[] = [
        {
          type: 'pdf',
          label: 'Original PDF',
          description: 'The original uploaded PDF document',
          icon: <PictureAsPdf color="error" />,
          available: data.pdf_available || false,
          size: data.pdf_size,
        },
        {
          type: 'docling_json',
          label: 'Raw Docling JSON',
          description: 'Raw extraction output from Docling service',
          icon: <Code color="primary" />,
          available: data.docling_json_available || false,
          size: data.docling_json_size,
        },
        {
          type: 'processed_json',
          label: 'Processed JSON',
          description: 'Cleaned and normalized document ready for embedding',
          icon: <DataObject color="success" />,
          available: data.processed_json_available || false,
          size: data.processed_json_size,
        },
      ];

      setDownloadableFiles(files);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (fileType: string) => {
    if (!documentId) return;

    try {
      const response = await fetch(
        `/api/weaviate/documents/${documentId}/download/${fileType}`,
        { method: 'GET' }
      );

      if (!response.ok) {
        throw new Error(`Failed to download ${fileType}`);
      }

      // Get filename from Content-Disposition header or use default
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = `${documentId}_${fileType}`;

      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (filenameMatch && filenameMatch[1]) {
          filename = filenameMatch[1].replace(/['"]/g, '');
        }
      }

      // Create blob and download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError(`Failed to download ${fileType}: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const formatFileSize = (bytes?: number): string => {
    if (bytes === undefined || bytes === null) return '';
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>
        Download Document Files
      </DialogTitle>
      <DialogContent>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress />
          </Box>
        ) : error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Select files to download. These represent different stages of document processing.
            </Typography>
            <List>
              {downloadableFiles.map((file) => (
                <ListItem key={file.type} disablePadding>
                  <ListItemButton
                    onClick={() => handleDownload(file.type)}
                    disabled={!file.available}
                  >
                    <ListItemIcon>{file.icon}</ListItemIcon>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <span>{file.label}</span>
                          {file.size && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              ({formatFileSize(file.size)})
                            </Typography>
                          )}
                          {!file.available && (
                            <Typography
                              variant="caption"
                              color="error"
                            >
                              Not available
                            </Typography>
                          )}
                        </Box>
                      }
                      secondary={file.description}
                    />
                    {file.available && (
                      <ListItemIcon>
                        <Download />
                      </ListItemIcon>
                    )}
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default DocumentDownloadDialog;