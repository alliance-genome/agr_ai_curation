import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Stack } from '@mui/material';
import DocumentDetailsDialog from '../../components/weaviate/DocumentDetailsDialog';
import { useDeleteDocument, useReembedDocument } from '../../services/weaviate';

const navigateBackPath = '/weaviate/documents';

const DocumentDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = React.useState(true);
  const [setupError, setSetupError] = React.useState<string | null>(null);
  const { mutateAsync: deleteDocument } = useDeleteDocument();
  const { mutateAsync: reembedDocument } = useReembedDocument();

  React.useEffect(() => {
    if (!id) {
      setSetupError('Missing document identifier');
    }
  }, [id]);

  const handleClose = React.useCallback(() => {
    setDialogOpen(false);
    navigate(navigateBackPath);
  }, [navigate]);

  const handleDelete = React.useCallback(
    async (documentId: string) => { await deleteDocument(documentId); },
    [deleteDocument],
  );

  const handleReembed = React.useCallback(
    async (documentId: string) => { await reembedDocument(documentId); },
    [reembedDocument],
  );

  if (!id) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error" sx={{ mb: 2 }}>
          {setupError ?? 'Document identifier was not provided.'}
        </Alert>
        <Button variant="contained" onClick={() => navigate(navigateBackPath)}>
          Back to Documents
        </Button>
      </Box>
    );
  }

  return (
    <Stack sx={{ p: 3 }}>
      <DocumentDetailsDialog
        open={dialogOpen}
        documentId={id}
        onClose={handleClose}
        onDelete={handleDelete}
        onReembed={handleReembed}
        onRefreshRequested={async () => undefined}
      />
    </Stack>
  );
};

export default DocumentDetailPage;
