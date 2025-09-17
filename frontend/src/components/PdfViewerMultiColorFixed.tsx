import { useState, useEffect, useRef } from "react";
import {
  Box,
  Paper,
  Button,
  Typography,
  CircularProgress,
  Alert,
} from "@mui/material";
import { Upload, Refresh } from "@mui/icons-material";
import { debug } from "../utils/debug";

// Default color palette for different highlight terms (fallback)
const DEFAULT_HIGHLIGHT_COLORS = [
  "#ffd54f", // Amber
  "#80deea", // Cyan
  "#c5e1a5", // Light Green
  "#f48fb1", // Pink
  "#ce93d8", // Purple
  "#90caf9", // Blue
  "#ffcc80", // Orange
  "#bcaaa4", // Brown
];

const DEFAULT_HIGHLIGHT_OPACITY = 0.4;
const SETTINGS_KEY = "alliance-user-settings";

interface HighlightSettings {
  highlightOpacity: number;
  highlightColors: string[];
}

interface PdfViewerMultiColorFixedProps {
  highlightTerms?: string[];
  onTextExtracted?: (textData: any) => void;
  onPdfUrlChange?: (url: string) => void;
  pdfUrl?: string;
}

function PdfViewerMultiColorFixed({
  highlightTerms = [],
  onPdfUrlChange,
  pdfUrl,
}: PdfViewerMultiColorFixedProps) {
  debug.pdfHighlight(
    "🚀 COMPONENT: PdfViewerMultiColorFixed function called/rendered",
  );
  debug.pdfHighlight("🚀 COMPONENT: Props received:", {
    highlightTerms,
    pdfUrl,
  });

  const [loading, setLoading] = useState(() => {
    debug.pdfHighlight("🚀 COMPONENT: useState loading initialized");
    return false;
  });
  const [error, setError] = useState<string | null>(() => {
    debug.pdfHighlight("🚀 COMPONENT: useState error initialized");
    return null;
  });
  const [currentPdfUrl, setCurrentPdfUrl] = useState(() => {
    const url = pdfUrl || "";
    debug.pdfHighlight(
      "🚀 COMPONENT: useState currentPdfUrl initialized to:",
      url,
    );
    return url;
  });
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pdfLoaded, setPdfLoaded] = useState(() => {
    debug.pdfHighlight("🚀 COMPONENT: useState pdfLoaded initialized to false");
    return false;
  });
  const [highlightSettings, setHighlightSettings] = useState<HighlightSettings>(
    () => {
      const initialSettings = {
        highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
        highlightColors: DEFAULT_HIGHLIGHT_COLORS,
      };
      debug.pdfHighlight(
        "🚀 COMPONENT: useState highlightSettings initialized:",
        initialSettings,
      );
      return initialSettings;
    },
  );

  // Store highlightTerms in a ref to avoid closure issues
  const highlightTermsRef = useRef(highlightTerms);
  useEffect(() => {
    debug.pdfHighlight(
      "🚀 COMPONENT: useEffect for highlightTermsRef update running, terms:",
      highlightTerms,
    );
    highlightTermsRef.current = highlightTerms;
  }, [highlightTerms]);

  // Track when pdfUrl prop changes
  useEffect(() => {
    if (!pdfUrl) {
      return;
    }
    debug.pdfHighlight(
      "📑 PDF COMPONENT: pdfUrl prop changed, updating currentPdfUrl",
      pdfUrl,
    );
    setCurrentPdfUrl(pdfUrl);
    // Loading occurs in the effect that reacts to currentPdfUrl changes
  }, [pdfUrl]);

  // Load settings from localStorage and listen for changes
  useEffect(() => {
    debug.pdfHighlight(
      "🔧 PDF COMPONENT: useEffect for loading settings is running",
    );

    const loadSettings = () => {
      debug.pdfHighlight("🔧 PDF COMPONENT: loadSettings function called");
      debug.pdfHighlight(
        "🔧 Loading settings from localStorage on PDF component mount",
      );
      const savedSettings = localStorage.getItem(SETTINGS_KEY);
      debug.pdfHighlight(
        "🔧 PDF COMPONENT: Retrieved from localStorage:",
        savedSettings,
      );

      if (savedSettings) {
        try {
          const parsed = JSON.parse(savedSettings);
          debug.pdfHighlight("🔧 PDF COMPONENT: Parsed settings:", parsed);

          const loadedSettings = {
            highlightOpacity:
              parsed.highlightOpacity || DEFAULT_HIGHLIGHT_OPACITY,
            highlightColors: parsed.highlightColors || DEFAULT_HIGHLIGHT_COLORS,
          };
          debug.pdfHighlight(
            "🔧 PDF COMPONENT: Setting loaded highlight settings:",
            loadedSettings,
          );
          setHighlightSettings(loadedSettings);

          // Update CSS styles in iframe immediately after loading settings
          debug.pdfHighlight(
            "🔧 PDF COMPONENT: Updating CSS styles with loaded settings",
          );
          setTimeout(() => {
            if (iframeRef.current?.contentDocument) {
              updateHighlightStyles(
                iframeRef.current.contentDocument,
                loadedSettings,
              );
            }
          }, 100);
        } catch (e) {
          debug.pdfHighlight(
            "❌ PDF COMPONENT: Failed to parse saved settings:",
            e,
          );
        }
      } else {
        debug.pdfHighlight(
          "🔧 PDF COMPONENT: No saved settings found, using defaults",
        );
      }
    };

    // Load settings on mount
    debug.pdfHighlight("🔧 PDF COMPONENT: About to call loadSettings()");
    loadSettings();

    // Listen for settings changes
    const handleSettingsChange = (event: any) => {
      debug.pdfHighlight(
        "🔧 PDF COMPONENT: Settings change event received:",
        event.detail,
      );
      if (event.detail) {
        const newSettings = {
          highlightOpacity:
            event.detail.highlightOpacity || DEFAULT_HIGHLIGHT_OPACITY,
          highlightColors:
            event.detail.highlightColors || DEFAULT_HIGHLIGHT_COLORS,
        };
        debug.pdfHighlight(
          "🔧 PDF COMPONENT: Setting new highlight settings:",
          newSettings,
        );
        setHighlightSettings(newSettings);

        // Re-apply highlights with new settings if PDF is loaded
        if (pdfLoaded && highlightTermsRef.current.length > 0) {
          debug.pdfHighlight(
            "🔧 PDF COMPONENT: Re-applying highlights with new settings",
          );
          setTimeout(() => {
            clearAllHighlights();
            setTimeout(() => applyHighlights(), 100);
          }, 50);
        } else {
          debug.pdfHighlight("🔧 PDF COMPONENT: Not re-applying highlights:", {
            pdfLoaded,
            termsLength: highlightTermsRef.current.length,
          });
        }
      } else {
        debug.pdfHighlight(
          "❌ PDF COMPONENT: Settings change event has no detail",
        );
      }
    };

    debug.pdfHighlight(
      "🔧 PDF COMPONENT: Adding settingsChanged event listener",
    );
    window.addEventListener("settingsChanged", handleSettingsChange);
    return () => {
      debug.pdfHighlight(
        "🔧 PDF COMPONENT: Removing settingsChanged event listener",
      );
      window.removeEventListener("settingsChanged", handleSettingsChange);
    };
  }, [pdfLoaded]);

  // Initialize PDF viewer on mount
  useEffect(() => {
    debug.pdfHighlight(
      "🚀 COMPONENT: useEffect for PDF viewer initialization/update running",
    );
    debug.pdfHighlight(
      "🚀 COMPONENT: currentPdfUrl for initialization:",
      currentPdfUrl,
    );
    if (currentPdfUrl) {
      debug.pdfHighlight(
        "🚀 COMPONENT: Calling loadPdfInViewer with:",
        currentPdfUrl,
      );
      loadPdfInViewer(currentPdfUrl);
    } else {
      debug.pdfHighlight("🚀 COMPONENT: No currentPdfUrl, skipping PDF load");
    }
  }, [currentPdfUrl]);

  // Load PDF in viewer
  const loadPdfInViewer = (url: string) => {
    debug.pdfHighlight("🚀 COMPONENT: loadPdfInViewer called with:", url);
    setLoading(true);
    setError(null);
    setPdfLoaded(false);
    debug.pdfRender(`Loading PDF in multi-color viewer: ${url}`);
    debug.pdfHighlight(
      "🚀 COMPONENT: Set loading=true, error=null, pdfLoaded=false",
    );

    if (!iframeRef.current) {
      debug.pdfHighlight("❌ COMPONENT: iframeRef.current is null!");
      return;
    }

    const normalizedUrl = url.startsWith("/") ? url : `/${url}`;
    const absoluteUrl = url.startsWith("http")
      ? url
      : `${window.location.origin}${normalizedUrl}`;

    console.log("🔍 PDF Loading Debug:", {
      originalUrl: url,
      normalizedUrl,
      absoluteUrl,
      windowOrigin: window.location.origin,
      encodedRelative: encodeURIComponent(normalizedUrl),
    });

    const encodedRelativeUrl = encodeURIComponent(normalizedUrl);
    const viewerUrl = `/pdfjs/web/viewer.html?file=${encodedRelativeUrl}`;
    debug.pdfHighlight("🚀 COMPONENT: Setting iframe src to:", viewerUrl);
    console.log("📄 Final viewer URL:", viewerUrl);
    console.log("📄 URL that PDF.js will load:", normalizedUrl);

    iframeRef.current.src = viewerUrl;

    if (onPdfUrlChange) {
      debug.pdfHighlight("🚀 COMPONENT: Calling onPdfUrlChange with:", url);
      onPdfUrlChange(url);
    } else {
      debug.pdfHighlight("🚀 COMPONENT: No onPdfUrlChange callback provided");
    }
  };

  // Inject mark.js and styles into iframe
  const injectMarkJsAndStyles = (iframeDoc: Document) => {
    // Check if already injected
    if (iframeDoc.getElementById("mark-js-script")) {
      // Update styles if they exist but settings may have changed
      updateHighlightStyles(iframeDoc);
      return;
    }

    // Inject mark.js script
    const markScript = iframeDoc.createElement("script");
    markScript.id = "mark-js-script";
    markScript.src =
      "https://cdn.jsdelivr.net/npm/mark.js@8.11.1/dist/mark.min.js";
    markScript.onload = () => {
      debug.pdfHighlight("mark.js loaded in iframe");
    };
    iframeDoc.head.appendChild(markScript);

    // Inject initial highlight styles
    updateHighlightStyles(iframeDoc);

    debug.pdfHighlight("mark.js and styles injected into iframe");
  };

  // Update highlight styles in iframe
  const updateHighlightStyles = (
    iframeDoc: Document,
    customSettings?: HighlightSettings,
  ) => {
    const settingsToUse = customSettings || highlightSettings;
    debug.pdfHighlight("🔍 updateHighlightStyles called");
    debug.pdfHighlight("🔍 Using settings:", settingsToUse);
    debug.pdfHighlight(
      "🔍 Colors count:",
      settingsToUse.highlightColors.length,
    );
    debug.pdfHighlight("🔍 Opacity value:", settingsToUse.highlightOpacity);
    debug.pdfHighlight("🔍 Colors array:", settingsToUse.highlightColors);

    let styleSheet = iframeDoc.getElementById(
      "highlight-styles",
    ) as HTMLStyleElement;
    if (!styleSheet) {
      debug.pdfHighlight("🔍 Creating new style sheet");
      styleSheet = iframeDoc.createElement("style");
      styleSheet.id = "highlight-styles";
      iframeDoc.head.appendChild(styleSheet);
    } else {
      debug.pdfHighlight("🔍 Using existing style sheet");
    }

    const generatedCSS = `
      /* Reset mark elements to not affect text positioning */
      mark {
        display: inline !important;
        padding: 0 !important;
        margin: 0 !important;
        line-height: inherit !important;
        font: inherit !important;
        letter-spacing: inherit !important;
        position: static !important;
        border: none !important;
        outline: none !important;
        text-decoration: none !important;
      }

      /* Apply background colors to mark elements - make them very vibrant */
      ${settingsToUse.highlightColors
        .map((color, index) => {
          // Make highlights much more vibrant - use higher opacity
          const makeVibrant = (hex: string, baseOpacity: number) => {
            // For vibrant highlighting, boost opacity significantly (min 0.8)
            const vibrancy = Math.max(0.8, Math.min(1.0, baseOpacity + 0.3));
            const r = parseInt(hex.slice(1, 3), 16);
            const g = parseInt(hex.slice(3, 5), 16);
            const b = parseInt(hex.slice(5, 7), 16);
            return `rgba(${r}, ${g}, ${b}, ${vibrancy})`;
          };

          return `
      .pdf-highlight-${index} {
        background-color: ${makeVibrant(color, settingsToUse.highlightOpacity)} !important;
        color: inherit !important;
        font-weight: inherit !important;
        text-shadow: none !important;
        position: static !important;
        z-index: auto !important;
        box-decoration-break: clone !important;
        -webkit-box-decoration-break: clone !important;
      }`;
        })
        .join("\n")}
    `;

    debug.pdfHighlight("🔍 Generated CSS length:", generatedCSS.length);
    debug.pdfHighlight(
      "🔍 Generated CSS sample:",
      generatedCSS.substring(0, 300) + "...",
    );

    styleSheet.textContent = generatedCSS;

    debug.pdfHighlight(
      "🔍 Style sheet updated, textContent length:",
      styleSheet.textContent.length,
    );
    debug.pdfHighlight(
      "🔍 Style sheet in DOM head:",
      !!iframeDoc.head.contains(styleSheet),
    );

    // Verify the styles are actually applied
    const allStyleSheets = Array.from(iframeDoc.styleSheets);
    debug.pdfHighlight(
      "🔍 Total stylesheets in iframe:",
      allStyleSheets.length,
    );

    debug.pdfHighlight("✅ Highlight styles updated");
  };

  // Apply highlights using mark.js
  const applyHighlights = (specificTextLayer?: HTMLElement) => {
    // Use ref to get current highlight terms to avoid closure issues
    const currentTerms = highlightTermsRef.current;

    if (!iframeRef.current?.contentWindow || currentTerms.length === 0) {
      debug.pdfHighlight(
        `Cannot apply highlights - iframe: ${!!iframeRef.current?.contentWindow}, terms: ${currentTerms.length}`,
      );
      return;
    }

    try {
      const iframeWindow = iframeRef.current.contentWindow as any;
      const iframeDoc = iframeWindow.document;

      // Wait for mark.js to be available
      if (!iframeWindow.Mark) {
        debug.pdfHighlight("Mark.js not yet loaded, retrying...");
        setTimeout(() => applyHighlights(specificTextLayer), 500);
        return;
      }

      // If a specific text layer is provided, only highlight that one
      // Otherwise, highlight all text layers
      const textLayers = specificTextLayer
        ? [specificTextLayer]
        : Array.from(iframeDoc.querySelectorAll(".textLayer"));

      debug.pdfHighlight(
        `Processing ${textLayers.length} text layer(s), specific layer: ${!!specificTextLayer}`,
      );

      textLayers.forEach((textLayer, index) => {
        const element = textLayer as HTMLElement;
        const pageDiv = element.closest(".page");
        const pageNum = pageDiv
          ? pageDiv.getAttribute("data-page-number")
          : "unknown";

        // Log text layer content info
        const textContent = element.textContent || "";
        debug.pdfHighlight(
          `Processing text layer ${index + 1}, page ${pageNum}, text length: ${textContent.length}`,
        );

        // Check if this text layer already has the correct highlights
        const existingMarks = element.querySelectorAll("mark");
        const hasCorrectHighlights =
          existingMarks.length > 0 &&
          currentTerms.every((term) =>
            Array.from(existingMarks).some((mark) =>
              mark.textContent?.toLowerCase().includes(term.toLowerCase()),
            ),
          );

        if (hasCorrectHighlights && !specificTextLayer) {
          debug.pdfHighlight(
            `Page ${pageNum}: Already has correct highlights, skipping`,
          );
          return;
        }

        debug.pdfHighlight(
          `Page ${pageNum}: Clearing existing marks (found ${existingMarks.length})`,
        );

        // Clear existing highlights before applying new ones
        const markInstance = new iframeWindow.Mark(textLayer);
        markInstance.unmark();

        debug.pdfHighlight(
          `Page ${pageNum}: Applying highlights for terms: ${currentTerms.join(", ")}`,
        );

        // Apply highlights for each term
        currentTerms.forEach((term, termIndex) => {
          const className = `pdf-highlight-${termIndex % highlightSettings.highlightColors.length}`;
          const expectedColor =
            highlightSettings.highlightColors[
              termIndex % highlightSettings.highlightColors.length
            ];
          const expectedOpacity = highlightSettings.highlightOpacity;

          debug.pdfHighlight(`🎨 Page ${pageNum}: Searching for "${term}"...`);
          debug.pdfHighlight(
            `🎨 Page ${pageNum}: Using className "${className}"`,
          );
          debug.pdfHighlight(
            `🎨 Page ${pageNum}: Expected color: ${expectedColor}, opacity: ${expectedOpacity}`,
          );
          debug.pdfHighlight(
            `🎨 Page ${pageNum}: Available colors: ${highlightSettings.highlightColors.length}`,
          );

          markInstance.mark(term, {
            className: className,
            caseSensitive: false,
            separateWordSearch: false,
            acrossElements: true,
            done: (counter: number) => {
              debug.pdfHighlight(
                `✅ Page ${pageNum}: Found and highlighted ${counter} instances of "${term}" with class "${className}"`,
              );

              // Check if the marks were actually created with the right class
              const createdMarks = element.querySelectorAll(
                `mark.${className}`,
              );
              debug.pdfHighlight(
                `🔍 Page ${pageNum}: Created ${createdMarks.length} mark elements with class "${className}"`,
              );

              if (createdMarks.length > 0) {
                const firstMark = createdMarks[0] as HTMLElement;
                const computedStyle = window.getComputedStyle(firstMark);
                debug.pdfHighlight(
                  `🔍 Page ${pageNum}: First mark computed backgroundColor: ${computedStyle.backgroundColor}`,
                );
                debug.pdfHighlight(
                  `🔍 Page ${pageNum}: First mark computed opacity: ${computedStyle.opacity}`,
                );
                debug.pdfHighlight(
                  `🔍 Page ${pageNum}: First mark classList: ${Array.from(firstMark.classList).join(", ")}`,
                );
              }
            },
            noMatch: () => {
              debug.pdfHighlight(
                `❌ Page ${pageNum}: No matches found for "${term}"`,
              );
            },
          });
        });
      });

      debug.pdfHighlight("Highlights application complete");
    } catch (error) {
      debug.error("PDF_HIGHLIGHT", "Error applying highlights:", error);
    }
  };

  // Handle iframe load
  useEffect(() => {
    const handleIframeLoad = () => {
      console.log("🚀 Iframe load event triggered");
      setLoading(false);
      setPdfLoaded(true);
      debug.pdfRender("PDF viewer loaded");

      if (iframeRef.current?.contentWindow) {
        const iframeWindow = iframeRef.current.contentWindow;
        const iframeDoc = iframeWindow.document;

        // Inject mark.js and styles
        injectMarkJsAndStyles(iframeDoc);

        // Wait for PDF.js to initialize
        const checkInterval = setInterval(() => {
          try {
            const PDFViewerApplication = (iframeWindow as any)
              .PDFViewerApplication;
            console.log("Checking PDFViewerApplication:", PDFViewerApplication);
            if (PDFViewerApplication && PDFViewerApplication.eventBus) {
              clearInterval(checkInterval);
              console.log("✅ PDFViewerApplication found and ready");

              // Listen for text layer rendered events - this is the key event
              // It fires for EACH page as its text layer is rendered
              PDFViewerApplication.eventBus.on(
                "textlayerrendered",
                (event: any) => {
                  debug.pdfHighlight(
                    `Text layer rendered for page ${event.pageNumber}`,
                  );

                  // Find the text layer for this specific page
                  setTimeout(() => {
                    const pageDiv = iframeDoc.querySelector(
                      `.page[data-page-number="${event.pageNumber}"]`,
                    );
                    debug.pdfHighlight(
                      `Page div found for page ${event.pageNumber}: ${!!pageDiv}`,
                    );

                    if (pageDiv) {
                      const textLayer = pageDiv.querySelector(
                        ".textLayer",
                      ) as HTMLElement;
                      debug.pdfHighlight(
                        `Text layer found for page ${event.pageNumber}: ${!!textLayer}`,
                      );

                      if (textLayer) {
                        // Log text content sample
                        const textContent = textLayer.textContent || "";
                        debug.pdfHighlight(
                          `Page ${event.pageNumber} text sample (first 200 chars): ${textContent.substring(0, 200)}`,
                        );
                        debug.pdfHighlight(
                          `Page ${event.pageNumber} contains "gene": ${textContent.toLowerCase().includes("gene")}`,
                        );

                        debug.pdfHighlight(
                          `Applying highlights to page ${event.pageNumber}`,
                        );
                        applyHighlights(textLayer);
                      } else {
                        debug.pdfHighlight(
                          `ERROR: Text layer not found for page ${event.pageNumber}`,
                        );
                      }
                    } else {
                      debug.pdfHighlight(
                        `ERROR: Page div not found for page ${event.pageNumber}`,
                      );
                    }
                  }, 50); // Small delay to ensure text layer is fully rendered
                },
              );

              // Also listen for document loaded to apply initial highlights
              PDFViewerApplication.eventBus.on("documentloaded", () => {
                debug.pdfHighlight(
                  "Document fully loaded, applying initial highlights",
                );
                setTimeout(() => applyHighlights(), 500);
              });

              // Initial highlight application for visible pages
              setTimeout(() => applyHighlights(), 1000);
            }
          } catch (e) {
            // Still waiting...
            console.log("Waiting for PDFViewerApplication...", e);
          }
        }, 100);
      }
    };

    const handleIframeError = (event: ErrorEvent) => {
      console.error("❌ Iframe error:", event);
      setError("Failed to load PDF viewer");
      setLoading(false);
    };

    const iframe = iframeRef.current;
    if (iframe) {
      iframe.addEventListener("load", handleIframeLoad);
      iframe.addEventListener("error", handleIframeError);
      return () => {
        iframe.removeEventListener("load", handleIframeLoad);
        iframe.removeEventListener("error", handleIframeError);
      };
    }
  }, []);

  // Clear all highlights function
  const clearAllHighlights = () => {
    if (!iframeRef.current?.contentWindow) return;

    try {
      const iframeWindow = iframeRef.current.contentWindow as any;
      const iframeDoc = iframeWindow.document;

      // Wait for mark.js to be available
      if (!iframeWindow.Mark) {
        debug.pdfHighlight("Mark.js not available for clearing");
        return;
      }

      // Get all text layers and clear highlights
      const textLayers = iframeDoc.querySelectorAll(".textLayer");
      debug.pdfHighlight(
        `Clearing highlights from ${textLayers.length} text layers`,
      );

      textLayers.forEach((textLayer: HTMLElement) => {
        const markInstance = new iframeWindow.Mark(textLayer);
        markInstance.unmark();
      });

      debug.pdfHighlight("All highlights cleared");
    } catch (error) {
      debug.error("PDF_HIGHLIGHT", "Error clearing highlights:", error);
    }
  };

  // Re-apply highlights when terms change
  useEffect(() => {
    if (pdfLoaded) {
      if (highlightTermsRef.current.length === 0) {
        debug.pdfHighlight("No highlight terms, clearing all highlights");
        clearAllHighlights();
      } else {
        debug.pdfHighlight(
          `Terms changed, re-applying highlights: ${highlightTermsRef.current.join(", ")}`,
        );
        // First clear all, then apply new highlights
        clearAllHighlights();
        setTimeout(() => applyHighlights(), 100);
      }
    }
  }, [highlightTerms, pdfLoaded]);

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type === "application/pdf") {
      debug.pdfRender(`User uploaded PDF: ${file.name}`);
      const fileUrl = URL.createObjectURL(file);
      setCurrentPdfUrl(fileUrl);
      loadPdfInViewer(fileUrl);
    }
  };

  const handleReset = () => {
    debug.pdfRender("Resetting to sample PDF");
    const defaultUrl = "/api/uploads/sample_fly_publication.pdf";
    setCurrentPdfUrl(defaultUrl);
    loadPdfInViewer(defaultUrl);
  };

  debug.pdfHighlight(
    "🚀 COMPONENT: Rendering PdfViewerMultiColorFixed, current state:",
    {
      loading,
      error,
      currentPdfUrl,
      pdfLoaded,
      highlightSettings,
      highlightTermsLength: highlightTerms.length,
    },
  );

  return (
    <Paper
      sx={{ height: "100%", display: "flex", flexDirection: "column", p: 2 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          PDF Viewer (Multi-Color)
        </Typography>

        <input
          type="file"
          accept="application/pdf"
          ref={fileInputRef}
          style={{ display: "none" }}
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
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {highlightTerms.map((term, index) => (
              <Box
                key={term}
                sx={{
                  px: 1,
                  py: 0.5,
                  borderRadius: 1,
                  backgroundColor:
                    highlightSettings.highlightColors[
                      index % highlightSettings.highlightColors.length
                    ],
                  opacity: highlightSettings.highlightOpacity,
                  color: "#000",
                  fontSize: "0.875rem",
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
          overflow: "hidden",
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
          bgcolor: "grey.100",
          position: "relative",
        }}
      >
        {loading && (
          <Box
            sx={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              zIndex: 1000,
            }}
          >
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
            width: "100%",
            height: "100%",
            border: "none",
          }}
          title="PDF Viewer"
        />
      </Box>
    </Paper>
  );
}

export default PdfViewerMultiColorFixed;
