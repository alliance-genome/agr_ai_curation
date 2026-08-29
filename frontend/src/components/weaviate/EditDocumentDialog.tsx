import React, { useState, useEffect } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
  CircularProgress,
} from '@mui/material';
import { Close } from '@mui/icons-material';

interface EditDocumentDialogProps {
  open: boolean;
  documentId: string;
  currentTitle: string | null;
  currentFilename: string;
  onClose: () => void;
  onSave: (documentId: string, title: string, filename: string) => Promise<void>;
}

const EditDocumentDialog: React.FC<EditDocumentDialogProps> = ({
  open,
  documentId,
  currentTitle,
  currentFilename,
  onClose,
  onSave,
}) => {
  const [title, setTitle] = useState(currentTitle ?? '');
  const [filename, setFilename] = useState(currentFilename);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset form state when dialog opens with new props
  useEffect(() => {
    if (open) {
      setTitle(currentTitle ?? '');
      setFilename(currentFilename);
      setError(null);
    }
  }, [open, currentFilename, currentTitle]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(documentId, title, filename);
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          Edit document metadata
          <IconButton onClick={onClose} size="small" aria-label="close">
            <Close fontSize="small" />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <TextField
          margin="dense"
          id="document-title"
          label="Display title"
          type="text"
          fullWidth
          variant="outlined"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          inputProps={{ maxLength: 255 }}
          helperText="Optional display title shown separately from the PDF filename."
          disabled={saving}
          sx={{ mt: 1 }}
        />
        <TextField
          required
          margin="dense"
          id="document-filename"
          label="PDF filename"
          type="text"
          fullWidth
          variant="outlined"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          inputProps={{ maxLength: 255 }}
          helperText="Must be a safe filename ending in .pdf."
          disabled={saving}
          sx={{ mt: 2 }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={saving || !filename.trim() || !filename.toLowerCase().endsWith('.pdf')}
          startIcon={saving ? <CircularProgress size={16} /> : undefined}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default EditDocumentDialog;
