import { useState, useEffect, useRef } from 'react';
import {
  Box,
  Paper,
  Button,
  Typography,
  CircularProgress,
  Alert,
} from '@mui/material';
import {
  Upload,
  Refresh,
} from '@mui/icons-material';
import { debug } from '../utils/debug';

// Color palette for different highlight terms
const HIGHLIGHT_COLORS = [
  '#ffd54f', // Amber
  '#80deea', // Cyan
  '#c5e1a5', // Light Green
  '#f48fb1', // Pink
  '#ce93d8', // Purple
  '#90caf9', // Blue
  '#ffcc80', // Orange
  '#bcaaa4', // Brown
];

interface HighlightTerm {
  term: string;
  color: string;
  className: string;
}

interface PdfViewerMultiColorFixedProps {
  highlightTerms?: string[];
  onTextExtracted?: (textData: any) => void;
  onPdfUrlChange?: (url: string) => void;
  pdfUrl?: string;
}

function PdfViewerMultiColorFixed({ highlightTerms = [], onTextExtracted, onPdfUrlChange, pdfUrl }: PdfViewerMultiColorFixedProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentPdfUrl, setCurrentPdfUrl] = useState(pdfUrl || '/api/uploads/sample_fly_publication.pdf');
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pdfLoaded, setPdfLoaded] = useState(false);

  // Initialize PDF viewer on mount
  useEffect(() => {
    if (currentPdfUrl) {
      loadPdfInViewer(currentPdfUrl);
    }
  }, []);

  // Load PDF in viewer
  const loadPdfInViewer = (url: string) => {
    setLoading(true);
    setError(null);
    setPdfLoaded(false);
    debug.pdfRender(`Loading PDF in multi-color viewer: ${url}`);
    
    if (iframeRef.current) {
      const viewerUrl = `/pdfjs/web/viewer.html?file=${encodeURIComponent(url)}`;
      iframeRef.current.src = viewerUrl;
      
      if (onPdfUrlChange) {
        onPdfUrlChange(url);
      }
    }
  };

  // Inject mark.js and styles into iframe
  const injectMarkJsAndStyles = (iframeDoc: Document) => {
    // Check if already injected
    if (iframeDoc.getElementById('mark-js-script')) {
      return;
    }

    // Inject mark.js script
    const markScript = iframeDoc.createElement('script');
    markScript.id = 'mark-js-script';
    markScript.src = 'https://cdn.jsdelivr.net/npm/mark.js@8.11.1/dist/mark.min.js';
    markScript.onload = () => {
      debug.pdfHighlight('mark.js loaded in iframe');
    };
    iframeDoc.head.appendChild(markScript);

    // Inject highlight styles
    const styleSheet = iframeDoc.createElement('style');
    styleSheet.id = 'highlight-styles';
    styleSheet.textContent = HIGHLIGHT_COLORS.map((color, index) => `
      .pdf-highlight-${index} {
        background-color: ${color} !important;
        color: #000 !important;
        padding: 1px 2px;
        border-radius: 2px;
      }
    `).join('\n');
    iframeDoc.head.appendChild(styleSheet);
    
    debug.pdfHighlight('Styles injected into iframe');
  };

  // Apply highlights using mark.js
  const applyHighlights = () => {
    if (!iframeRef.current?.contentWindow || highlightTerms.length === 0) return;
    
    try {
      const iframeWindow = iframeRef.current.contentWindow as any;
      const iframeDoc = iframeWindow.document;
      
      // Wait for mark.js to be available
      if (!iframeWindow.Mark) {
        debug.pdfHighlight('Mark.js not yet loaded, retrying...');
        setTimeout(applyHighlights, 500);
        return;
      }
      
      // Get all text layers
      const textLayers = iframeDoc.querySelectorAll('.textLayer');
      
      debug.pdfHighlight(`Found ${textLayers.length} text layers`);
      
      textLayers.forEach((textLayer: HTMLElement, pageIndex: number) => {
        // Clear existing highlights
        const markInstance = new iframeWindow.Mark(textLayer);
        markInstance.unmark();
        
        // Apply highlights for each term
        highlightTerms.forEach((term, termIndex) => {
          const className = `pdf-highlight-${termIndex % HIGHLIGHT_COLORS.length}`;
          
          markInstance.mark(term, {
            className: className,
            caseSensitive: false,
            separateWordSearch: false,
            acrossElements: true,
            done: (counter: number) => {
              if (counter > 0) {
                debug.pdfHighlight(`Highlighted ${counter} instances of "${term}" on page ${pageIndex + 1}`);
              }
            }
          });
        });
      });
      
      debug.pdfHighlight('All highlights applied');
    } catch (error) {
      debug.error('PDF_HIGHLIGHT', 'Error applying highlights:', error);
    }
  };

  // Handle iframe load
  useEffect(() => {
    const handleIframeLoad = () => {
      setLoading(false);
      setPdfLoaded(true);
      debug.pdfRender('PDF viewer loaded');
      
      if (iframeRef.current?.contentWindow) {
        const iframeWindow = iframeRef.current.contentWindow;
        const iframeDoc = iframeWindow.document;
        
        // Inject mark.js and styles
        injectMarkJsAndStyles(iframeDoc);
        
        // Wait for PDF.js to initialize
        const checkInterval = setInterval(() => {
          try {
            const PDFViewerApplication = (iframeWindow as any).PDFViewerApplication;
            if (PDFViewerApplication && PDFViewerApplication.eventBus) {
              clearInterval(checkInterval);
              
              // Listen for page rendered events
              PDFViewerApplication.eventBus.on('pagerendered', () => {
                debug.pdfHighlight('Page rendered, applying highlights');
                setTimeout(applyHighlights, 100);
              });
              
              // Listen for text layer rendered events
              PDFViewerApplication.eventBus.on('textlayerrendered', () => {
                debug.pdfHighlight('Text layer rendered, applying highlights');
                setTimeout(applyHighlights, 100);
              });
              
              // Initial highlight application
              setTimeout(applyHighlights, 1000);
            }
          } catch (e) {
            // Still waiting...
          }
        }, 100);
      }
    };

    const iframe = iframeRef.current;
    if (iframe) {
      iframe.addEventListener('load', handleIframeLoad);
      return () => iframe.removeEventListener('load', handleIframeLoad);
    }
  }, []);

  // Re-apply highlights when terms change
  useEffect(() => {
    if (pdfLoaded) {
      debug.pdfHighlight(`Terms changed, re-applying highlights: ${highlightTerms.join(', ')}`);
      applyHighlights();
    }
  }, [highlightTerms, pdfLoaded]);

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type === 'application/pdf') {
      debug.pdfRender(`User uploaded PDF: ${file.name}`);
      const fileUrl = URL.createObjectURL(file);
      setCurrentPdfUrl(fileUrl);
      loadPdfInViewer(fileUrl);
    }
  };

  const handleReset = () => {
    debug.pdfRender('Resetting to sample PDF');
    const defaultUrl = '/api/uploads/sample_fly_publication.pdf';
    setCurrentPdfUrl(defaultUrl);
    loadPdfInViewer(defaultUrl);
  };

  return (
    <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          PDF Viewer (Multi-Color)
        </Typography>

        <input
          type="file"
          accept="application/pdf"
          ref={fileInputRef}
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

      {highlightTerms.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="subtitle2" gutterBottom>
            Highlighting:
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {highlightTerms.map((term, index) => (
              <Box
                key={term}
                sx={{
                  px: 1,
                  py: 0.5,
                  borderRadius: 1,
                  backgroundColor: HIGHLIGHT_COLORS[index % HIGHLIGHT_COLORS.length],
                  color: '#000',
                  fontSize: '0.875rem',
                }}
              >
                {term}
              </Box>
            ))}
          </Box>
        </Box>
      )}


      <Box
        sx={{
          flexGrow: 1,
          overflow: 'hidden',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'flex-start',
          bgcolor: 'grey.100',
          position: 'relative',
        }}
      >
        {loading && (
          <Box sx={{ 
            position: 'absolute', 
            top: '50%', 
            left: '50%', 
            transform: 'translate(-50%, -50%)',
            zIndex: 1000
          }}>
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ maxWidth: 400 }}>
            {error}
          </Alert>
        )}

        <iframe
          ref={iframeRef}
          style={{
            width: '100%',
            height: '100%',
            border: 'none',
          }}
          title="PDF Viewer"
        />
      </Box>
    </Paper>
  );
}

export default PdfViewerMultiColorFixed;