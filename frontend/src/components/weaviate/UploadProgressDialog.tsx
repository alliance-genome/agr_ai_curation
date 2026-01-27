import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  LinearProgress,
  Typography,
  Box,
  Button,
  Stepper,
  Step,
  StepLabel,
  StepContent,
} from '@mui/material';
import {
  CloudUpload,
  Description,
  AutoAwesome,
  Storage,
  CheckCircle,
  Error,
  FileOpen,
} from '@mui/icons-material';

interface UploadProgressDialogProps {
  open: boolean;
  fileName: string;
  stage: string;
  progress: number;
  message: string;
  onClose: () => void;
  documentId?: string;
  onLoadForChat?: (documentId: string) => void;
}

const processingSteps = [
  { label: 'Uploading', icon: CloudUpload, stage: 'uploading' },
  { label: 'Parsing PDF', icon: Description, stage: 'parsing' },
  { label: 'Chunking Content', icon: Description, stage: 'chunking' },
  { label: 'Generating Embeddings', icon: AutoAwesome, stage: 'embedding' },
  { label: 'Storing in Database', icon: Storage, stage: 'storing' },
  { label: 'Complete', icon: CheckCircle, stage: 'completed' },
];

const UploadProgressDialog: React.FC<UploadProgressDialogProps> = ({
  open,
  fileName,
  stage,
  progress,
  message,
  onClose,
  documentId,
  onLoadForChat,
}) => {
  const getActiveStep = () => {
    const index = processingSteps.findIndex((step) => step.stage === stage);
    return index >= 0 ? index : 0;
  };

  const activeStep = getActiveStep();
  const isError = stage === 'error' || stage === 'failed';
  const isComplete = stage === 'completed';

  // Clean up technical messages for a more professional display
  const cleanMessage = (msg: string): string => {
    // Remove "awaiting update" messages and elapsed time
    const cleaned = msg.replace(/\.\.\.\s*awaiting update.*$/i, '...');
    return cleaned;
  };

  return (
    <Dialog
      open={open}
      onClose={isComplete || isError ? onClose : undefined}
      maxWidth="sm"
      fullWidth
      disableEscapeKeyDown={!isComplete && !isError}
    >
      <DialogTitle>
        {isError ? 'Upload Failed' : isComplete ? 'Upload Complete' : 'Processing Document'}
      </DialogTitle>
      <DialogContent>
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            {fileName}
          </Typography>
          <Typography variant="body1" sx={{ mt: 1 }}>
            {cleanMessage(message)}
          </Typography>
        </Box>

        <Box sx={{ mb: 3 }}>
          <LinearProgress
            variant="determinate"
            value={progress}
            sx={{
              height: 8,
              borderRadius: 4,
              backgroundColor: isError ? 'error.light' : undefined,
              '& .MuiLinearProgress-bar': {
                backgroundColor: isError ? 'error.main' : isComplete ? 'success.main' : 'primary.main',
              },
            }}
          />
          <Typography variant="body2" color="text.secondary" align="center" sx={{ mt: 1 }}>
            {progress}%
          </Typography>
        </Box>

        {!isError && (
          <Stepper activeStep={activeStep} orientation="vertical">
            {processingSteps.map((step, index) => {
              const Icon = step.icon;
              const stepCompleted = index < activeStep || isComplete;
              const stepActive = index === activeStep && !isComplete;

              return (
                <Step key={step.label} completed={stepCompleted}>
                  <StepLabel
                    icon={
                      <Icon
                        sx={{
                          color: stepCompleted
                            ? 'success.main'
                            : stepActive
                            ? 'primary.main'
                            : 'text.disabled',
                        }}
                      />
                    }
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        color: stepCompleted || stepActive ? 'text.primary' : 'text.disabled',
                        fontWeight: stepActive ? 'bold' : 'normal',
                      }}
                    >
                      {step.label}
                    </Typography>
                  </StepLabel>
                  {stepActive && !isComplete && (
                    <StepContent>
                      <Typography variant="caption" color="text.secondary">
                        Processing...
                      </Typography>
                    </StepContent>
                  )}
                </Step>
              );
            })}
          </Stepper>
        )}

        {isError && (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', mt: 2 }}>
            <Error color="error" sx={{ fontSize: 48 }} />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        {isComplete && documentId && onLoadForChat && (
          <Button
            onClick={() => onLoadForChat(documentId)}
            variant="contained"
            color="success"
            startIcon={<FileOpen />}
            sx={{ mr: 1 }}
          >
            Load for Chat
          </Button>
        )}
        {(isComplete || isError) && (
          <Button onClick={onClose} variant={isComplete ? 'outlined' : 'contained'} color={isError ? 'error' : 'primary'}>
            Close
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default UploadProgressDialog;