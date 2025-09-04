import { useState, useEffect, useRef } from 'react';
import {
  Box,
  Paper,
  IconButton,
  Button,
  Typography,
  Slider,
  Tooltip,
  CircularProgress,
  Alert,
} from '@mui/material';
import {
  NavigateBefore,
  NavigateNext,
  ZoomIn,
  ZoomOut,
  Upload,
  Refresh,
} from '@mui/icons-material';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf';

// Configure PDF.js worker - use local worker file
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

function PdfViewer() {
  const [pdf, setPdf] = useState<any>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [zoom, setZoom] = useState(1.2);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const renderTaskRef = useRef<any>(null);

  const loadPdf = async (url: string) => {
    try {
      setLoading(true);
      setError(null);
      const loadingTask = pdfjsLib.getDocument(url);
      const pdfDoc = await loadingTask.promise;
      setPdf(pdfDoc);
      setTotalPages(pdfDoc.numPages);
      setCurrentPage(1);
    } catch (err) {
      setError('Failed to load PDF. Please try again.');
      console.error('Error loading PDF:', err);
    } finally {
      setLoading(false);
    }
  };

  const renderPage = async () => {
    if (!pdf || !canvasRef.current) return;

    try {
      // Cancel any existing render task
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }

      const page = await pdf.getPage(currentPage);
      const viewport = page.getViewport({ scale: zoom });
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');

      // Clear the canvas before rendering
      context.clearRect(0, 0, canvas.width, canvas.height);
      
      canvas.height = viewport.height;
      canvas.width = viewport.width;

      const renderContext = {
        canvasContext: context,
        viewport: viewport,
      };

      renderTaskRef.current = page.render(renderContext);
      await renderTaskRef.current.promise;
      renderTaskRef.current = null;
    } catch (err) {
      if ((err as any).name !== 'RenderingCancelledException') {
        console.error('Error rendering page:', err);
      }
    }
  };

  useEffect(() => {
    loadPdf('/api/uploads/sample_fly_publication.pdf');
    
    // Cleanup on unmount
    return () => {
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
      }
    };
  }, []);

  useEffect(() => {
    if (pdf) {
      renderPage();
    }
  }, [pdf, currentPage, zoom]);

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type === 'application/pdf') {
      const fileUrl = URL.createObjectURL(file);
      loadPdf(fileUrl);
    }
  };

  const handlePrevPage = () => {
    if (currentPage > 1) {
      setCurrentPage(currentPage - 1);
    }
  };

  const handleNextPage = () => {
    if (currentPage < totalPages) {
      setCurrentPage(currentPage + 1);
    }
  };

  const handleZoomChange = (_: Event, value: number | number[]) => {
    setZoom(value as number);
  };

  const handleReset = () => {
    loadPdf('/api/uploads/sample_fly_publication.pdf');
  };

  return (
    <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <Tooltip title="Previous Page">
          <span>
            <IconButton onClick={handlePrevPage} disabled={currentPage <= 1}>
              <NavigateBefore />
            </IconButton>
          </span>
        </Tooltip>

        <Typography variant="body2" sx={{ minWidth: '60px', textAlign: 'center' }}>
          {currentPage} / {totalPages}
        </Typography>

        <Tooltip title="Next Page">
          <span>
            <IconButton onClick={handleNextPage} disabled={currentPage >= totalPages}>
              <NavigateNext />
            </IconButton>
          </span>
        </Tooltip>

        <Box sx={{ mx: 2, height: 24, borderLeft: 1, borderColor: 'divider' }} />

        <Tooltip title="Zoom Out">
          <IconButton onClick={() => setZoom(Math.max(0.5, zoom - 0.1))}>
            <ZoomOut />
          </IconButton>
        </Tooltip>

        <Slider
          value={zoom}
          onChange={handleZoomChange}
          min={0.5}
          max={2}
          step={0.1}
          sx={{ width: 100 }}
          valueLabelDisplay="auto"
          valueLabelFormat={(value) => `${Math.round(value * 100)}%`}
        />

        <Tooltip title="Zoom In">
          <IconButton onClick={() => setZoom(Math.min(2, zoom + 0.1))}>
            <ZoomIn />
          </IconButton>
        </Tooltip>

        <Box sx={{ flexGrow: 1 }} />

        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          style={{ display: 'none' }}
          onChange={handleFileUpload}
        />

        <Button
          variant="outlined"
          size="small"
          startIcon={<Upload />}
          onClick={() => fileInputRef.current?.click()}
        >
          Upload
        </Button>

        <Button
          variant="outlined"
          size="small"
          startIcon={<Refresh />}
          onClick={handleReset}
        >
          Reset
        </Button>
      </Box>

      <Box sx={{ flexGrow: 1, overflow: 'auto', position: 'relative' }}>
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ m: 2 }}>
            {error}
          </Alert>
        )}

        {!loading && !error && (
          <canvas
            ref={canvasRef}
            style={{
              display: 'block',
              margin: '0 auto',
            }}
          />
        )}
      </Box>
    </Paper>
  );
}

export default PdfViewer;