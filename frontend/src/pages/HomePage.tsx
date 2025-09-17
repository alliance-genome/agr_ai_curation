import {
  AppBar,
  Toolbar,
  Typography,
  Button,
  Box,
  IconButton,
  Paper,
} from "@mui/material";
import {
  Home as HomeIcon,
  AdminPanelSettings as AdminIcon,
  Settings as SettingsIcon,
  Description,
  Brightness4,
  Brightness7,
} from "@mui/icons-material";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useNavigate } from "react-router-dom";
import { useTheme } from "@mui/material/styles";
import { useRef, useState } from "react";
import PdfViewerMultiColorFixed from "../components/PdfViewerMultiColorFixed";
import ChatInterface from "../components/ChatInterface";
import PDFUpload from "../components/PDFUpload";
import CurationPanel from "../components/CurationPanel";
import { PdfTextData } from "../types/pdf";
import { debug } from "../utils/debug";

interface HomePageProps {
  toggleColorMode: () => void;
}

const PANEL_SIZES_KEY = "alliance-panel-sizes";

function HomePage({ toggleColorMode }: HomePageProps) {
  debug.pdfHighlight("🏠 HOMEPAGE: Component function called/rendered");

  const navigate = useNavigate();
  const theme = useTheme();
  const panelGroupRef = useRef<any>(null);
  const [highlightTerms, setHighlightTerms] = useState<string[]>([]);
  const [pdfTextData, setPdfTextData] = useState<PdfTextData | null>(null);
  const [currentPdfUrl, setCurrentPdfUrl] = useState<string | null>(null);
  const [uploadedPdfId, setUploadedPdfId] = useState<string | null>(null);

  debug.pdfHighlight("🏠 HOMEPAGE: State initialized:", {
    highlightTerms,
    currentPdfUrl,
  });

  // Load saved panel sizes from localStorage
  const getSavedPanelSizes = () => {
    const saved = localStorage.getItem(PANEL_SIZES_KEY);
    if (saved) {
      try {
        return JSON.parse(saved);
      } catch {
        return null;
      }
    }
    return null;
  };

  // Save panel sizes to localStorage when they change
  const handlePanelResize = (sizes: number[]) => {
    localStorage.setItem(PANEL_SIZES_KEY, JSON.stringify(sizes));
  };

  // Handle highlight requests from the TEST tab
  const handleHighlight = (searchTerm: string) => {
    setHighlightTerms((prev) => [...prev, searchTerm]);
  };

  const handleClearHighlights = () => {
    setHighlightTerms([]);
  };

  // Handle PDF text extraction
  const handlePdfTextExtracted = (textData: PdfTextData) => {
    setPdfTextData(textData);
    console.log("PDF text extracted:", {
      totalPages: textData.totalPages,
      fullTextLength: textData.fullText.length,
      firstPageSample: textData.fullText.substring(0, 200),
    });
  };

  // Handle PDF URL change
  const handlePdfUrlChange = (url: string) => {
    setCurrentPdfUrl(url);
    setHighlightTerms([]);
  };

  const handlePdfUploaded = ({
    pdfId,
    viewerUrl,
  }: {
    pdfId: string;
    viewerUrl?: string;
  }) => {
    setUploadedPdfId(pdfId);
    if (viewerUrl) {
      setCurrentPdfUrl(viewerUrl);
    }
    setHighlightTerms([]);
  };

  debug.pdfHighlight("🏠 HOMEPAGE: About to render HomePage");

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <AppBar
        position="static"
        sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}
      >
        <Toolbar>
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, cursor: "pointer" }}
            onClick={() => navigate("/")}
          >
            Alliance AI-Assisted Curation Interface
          </Typography>

          <IconButton onClick={toggleColorMode} color="inherit" sx={{ mr: 1 }}>
            {theme.palette.mode === "dark" ? <Brightness7 /> : <Brightness4 />}
          </IconButton>

          <Button
            color="inherit"
            startIcon={<HomeIcon />}
            onClick={() => navigate("/")}
            sx={{ mr: 1 }}
          >
            Home
          </Button>

          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => navigate("/settings")}
            sx={{ mr: 1 }}
          >
            Settings
          </Button>

          <Button
            color="inherit"
            startIcon={<Description />}
            onClick={() => navigate("/browser")}
            sx={{ mr: 1 }}
          >
            Browser
          </Button>

          <Button
            color="inherit"
            variant="outlined"
            startIcon={<AdminIcon />}
            onClick={() => navigate("/admin")}
          >
            Admin
          </Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ flexGrow: 1, display: "flex", overflow: "hidden" }}>
        <PanelGroup
          direction="horizontal"
          style={{ height: "100%" }}
          onLayout={handlePanelResize}
          ref={panelGroupRef}
        >
          <Panel
            defaultSize={getSavedPanelSizes()?.[0] || 33}
            minSize={20}
            maxSize={50}
          >
            <Box
              sx={{
                p: 2,
                display: "flex",
                flexDirection: "column",
                gap: 2,
                height: "100%",
              }}
            >
              <PDFUpload onUploaded={handlePdfUploaded} />
              <Box sx={{ flexGrow: 1, minHeight: 0 }}>
                {currentPdfUrl ? (
                  (() => {
                    debug.pdfHighlight(
                      "🏠 HOMEPAGE: Rendering PdfViewerMultiColorFixed with props:",
                      {
                        highlightTerms,
                        currentPdfUrl,
                        hasOnTextExtracted: !!handlePdfTextExtracted,
                        hasOnPdfUrlChange: !!handlePdfUrlChange,
                      },
                    );
                    return (
                      <PdfViewerMultiColorFixed
                        highlightTerms={highlightTerms}
                        onTextExtracted={handlePdfTextExtracted}
                        onPdfUrlChange={handlePdfUrlChange}
                        pdfUrl={currentPdfUrl}
                      />
                    );
                  })()
                ) : (
                  <Paper
                    elevation={0}
                    sx={{
                      p: 4,
                      textAlign: "center",
                      height: "100%",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Typography variant="body1" color="text.secondary">
                      Upload a PDF to preview it here.
                    </Typography>
                  </Paper>
                )}
              </Box>
            </Box>
          </Panel>

          <PanelResizeHandle
            style={{
              width: "4px",
              backgroundColor: theme.palette.divider,
              cursor: "col-resize",
              transition: "background-color 0.2s",
            }}
            onMouseEnter={(e) => {
              (
                e.currentTarget as unknown as HTMLElement
              ).style.backgroundColor = theme.palette.primary.main;
            }}
            onMouseLeave={(e) => {
              (
                e.currentTarget as unknown as HTMLElement
              ).style.backgroundColor = theme.palette.divider;
            }}
          />

          <Panel
            defaultSize={getSavedPanelSizes()?.[1] || 34}
            minSize={20}
            maxSize={60}
          >
            <Box sx={{ p: 2, height: "100%" }}>
              <ChatInterface
                key={uploadedPdfId ?? "no-pdf"}
                pdfId={uploadedPdfId}
              />
            </Box>
          </Panel>

          <PanelResizeHandle
            style={{
              width: "4px",
              backgroundColor: theme.palette.divider,
              cursor: "col-resize",
              transition: "background-color 0.2s",
            }}
            onMouseEnter={(e) => {
              (
                e.currentTarget as unknown as HTMLElement
              ).style.backgroundColor = theme.palette.primary.main;
            }}
            onMouseLeave={(e) => {
              (
                e.currentTarget as unknown as HTMLElement
              ).style.backgroundColor = theme.palette.divider;
            }}
          />

          <Panel
            defaultSize={getSavedPanelSizes()?.[2] || 33}
            minSize={20}
            maxSize={50}
          >
            <CurationPanel
              onHighlight={handleHighlight}
              onClearHighlights={handleClearHighlights}
              pdfTextData={pdfTextData}
            />
          </Panel>
        </PanelGroup>
      </Box>
    </Box>
  );
}

export default HomePage;
