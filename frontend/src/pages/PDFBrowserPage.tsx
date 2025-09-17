import { useEffect, useState } from "react";
import {
  AppBar,
  Toolbar,
  IconButton,
  Button,
  Box,
  Paper,
  Typography,
  List,
  ListItemButton,
  ListItemText,
  Divider,
  Tabs,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Alert,
  CircularProgress,
  Chip,
  Stack,
} from "@mui/material";
import {
  Home as HomeIcon,
  AdminPanelSettings as AdminIcon,
  Settings as SettingsIcon,
  Description,
  Brightness4,
  Brightness7,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";

interface DocumentSummary {
  id: string;
  filename: string;
  upload_timestamp: string;
  last_accessed?: string;
  page_count?: number;
  chunk_count?: number;
  table_count?: number;
  figure_count?: number;
  embeddings_generated?: boolean;
}

interface DocumentDetail extends DocumentSummary {
  file_size?: number;
  extraction_method?: string;
  preproc_version?: string;
  meta_data: Record<string, unknown>;
}

interface ChunkRow {
  id: string;
  chunk_index: number;
  text_preview: string;
  page_start?: number;
  page_end?: number;
  section_path?: string;
  element_type?: string;
  is_reference?: boolean;
  is_caption?: boolean;
  is_table?: boolean;
  is_figure?: boolean;
  token_count?: number;
}

interface LangGraphRunRow {
  id: string;
  workflow_name: string;
  input_query: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  latency_ms?: number;
  specialists_invoked: string[];
}

interface LangGraphNodeRow {
  id: string;
  graph_run_id: string;
  node_key: string;
  node_type: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  latency_ms?: number;
  error?: string;
}

interface EmbeddingSummaryRow {
  model_name: string;
  count: number;
  latest_created_at?: string;
}

interface PDFBrowserPageProps {
  toggleColorMode: () => void;
}

function formatDate(value?: string) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

const TABS = [
  "Overview",
  "Chunks",
  "LangGraph Runs",
  "LangGraph Nodes",
] as const;

function PDFBrowserPage({ toggleColorMode }: PDFBrowserPageProps) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selectedDocument, setSelectedDocument] =
    useState<DocumentDetail | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunkRow[]>([]);
  const [runs, setRuns] = useState<LangGraphRunRow[]>([]);
  const [nodes, setNodes] = useState<LangGraphNodeRow[]>([]);
  const [embeddingSummary, setEmbeddingSummary] = useState<
    EmbeddingSummaryRow[]
  >([]);
  const [tabIndex, setTabIndex] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const theme = useTheme();
  const navigate = useNavigate();

  useEffect(() => {
    const loadDocuments = async () => {
      try {
        const response = await fetch("/api/pdf-data/documents");
        if (!response.ok) {
          throw new Error("Failed to load documents");
        }
        const payload: DocumentSummary[] = await response.json();
        setDocuments(payload);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Unknown error";
        setError(message);
      }
    };

    loadDocuments();
  }, []);

  const handleSelectDocument = async (doc: DocumentSummary) => {
    setLoading(true);
    setError(null);
    setSelectedRun(null);
    try {
      const [detailResp, chunksResp, runsResp, embeddingsResp] =
        await Promise.all([
          fetch(`/api/pdf-data/documents/${doc.id}`),
          fetch(`/api/pdf-data/documents/${doc.id}/chunks`),
          fetch(`/api/pdf-data/documents/${doc.id}/langgraph-runs`),
          fetch(`/api/pdf-data/documents/${doc.id}/embeddings`),
        ]);

      if (!detailResp.ok) throw new Error("Failed to load document details");
      const detail = (await detailResp.json()) as DocumentDetail;
      setSelectedDocument(detail);

      setChunks(chunksResp.ok ? await chunksResp.json() : []);
      setRuns(runsResp.ok ? await runsResp.json() : []);
      setEmbeddingSummary(embeddingsResp.ok ? await embeddingsResp.json() : []);

      setTabIndex(0);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectRun = async (run: LangGraphRunRow) => {
    setSelectedRun(run.id);
    try {
      const response = await fetch(
        `/api/pdf-data/langgraph-runs/${run.id}/nodes`,
      );
      if (!response.ok) {
        throw new Error("Failed to load LangGraph node runs");
      }
      const payload: LangGraphNodeRow[] = await response.json();
      setNodes(payload);
      setTabIndex(3);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setError(message);
    }
  };

  return (
    <Box sx={{ display: "flex", height: "100vh", flexDirection: "column" }}>
      <AppBar position="static" sx={{ zIndex: (t) => t.zIndex.drawer + 1 }}>
        <Toolbar>
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, cursor: "pointer" }}
            onClick={() => navigate("/")}
          >
            PDF Data Browser
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

      <Box sx={{ display: "flex", flexGrow: 1, overflow: "hidden" }}>
        <Paper
          elevation={1}
          sx={{ width: 320, overflowY: "auto", borderRadius: 0 }}
        >
          <Box sx={{ p: 2 }}>
            <Typography variant="h6">Uploaded PDFs</Typography>
            <Typography variant="body2" color="text.secondary">
              Select a document to explore its chunks, embeddings, and LangGraph
              runs.
            </Typography>
          </Box>
          <Divider />
          <List dense disablePadding>
            {documents.map((doc) => (
              <ListItemButton
                key={doc.id}
                selected={selectedDocument?.id === doc.id}
                onClick={() => handleSelectDocument(doc)}
              >
                <ListItemText
                  primary={doc.filename}
                  secondary={`Uploaded ${formatDate(doc.upload_timestamp)}`}
                />
              </ListItemButton>
            ))}
          </List>
        </Paper>

        <Box sx={{ flexGrow: 1, p: 3, overflow: "auto" }}>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          {!selectedDocument && !loading && (
            <Typography variant="body1" color="text.secondary">
              Select a document from the left to view details.
            </Typography>
          )}

          {loading && (
            <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
              <CircularProgress size={20} />
              <Typography variant="body2">Loading document data…</Typography>
            </Box>
          )}

          {selectedDocument && !loading && (
            <Paper sx={{ p: 2 }}>
              <Tabs
                value={tabIndex}
                onChange={(_e, idx) => setTabIndex(idx)}
                sx={{ mb: 2 }}
              >
                {TABS.map((tab) => (
                  <Tab key={tab} label={tab} />
                ))}
              </Tabs>

              {tabIndex === 0 && (
                <Stack spacing={2}>
                  <Typography variant="h6">Overview</Typography>
                  <Stack direction="row" spacing={2} flexWrap="wrap">
                    <Box>
                      <Typography variant="subtitle2">Filename</Typography>
                      <Typography>{selectedDocument.filename}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="subtitle2">Uploaded</Typography>
                      <Typography>
                        {formatDate(selectedDocument.upload_timestamp)}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="subtitle2">Extraction</Typography>
                      <Typography>
                        {selectedDocument.extraction_method ?? "—"}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="subtitle2">Chunks</Typography>
                      <Typography>
                        {selectedDocument.chunk_count ?? 0}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="subtitle2">Embeddings</Typography>
                      <Typography>
                        {embeddingSummary.reduce(
                          (sum, row) => sum + row.count,
                          0,
                        )}
                      </Typography>
                    </Box>
                  </Stack>
                  <Divider />
                  <Typography variant="subtitle2">Embedding Summary</Typography>
                  {embeddingSummary.length === 0 ? (
                    <Typography color="text.secondary">
                      No embedding rows recorded yet.
                    </Typography>
                  ) : (
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Model</TableCell>
                          <TableCell>Count</TableCell>
                          <TableCell>Latest Created</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {embeddingSummary.map((row) => (
                          <TableRow key={row.model_name}>
                            <TableCell>{row.model_name}</TableCell>
                            <TableCell>{row.count}</TableCell>
                            <TableCell>
                              {formatDate(row.latest_created_at)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                  <Divider />
                  <Typography variant="subtitle2">Metadata</Typography>
                  <pre style={{ margin: 0 }}>
                    {JSON.stringify(selectedDocument.meta_data, null, 2)}
                  </pre>
                </Stack>
              )}

              {tabIndex === 1 && (
                <Box sx={{ overflow: "auto" }}>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Index</TableCell>
                        <TableCell>Page</TableCell>
                        <TableCell>Section</TableCell>
                        <TableCell>Element</TableCell>
                        <TableCell>Flags</TableCell>
                        <TableCell>Tokens</TableCell>
                        <TableCell>Preview</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {chunks.map((chunk) => (
                        <TableRow key={chunk.id} hover>
                          <TableCell>{chunk.chunk_index}</TableCell>
                          <TableCell>
                            {chunk.page_start === chunk.page_end
                              ? chunk.page_start
                              : `${chunk.page_start}–${chunk.page_end}`}
                          </TableCell>
                          <TableCell>{chunk.section_path || "—"}</TableCell>
                          <TableCell>{chunk.element_type || "—"}</TableCell>
                          <TableCell>
                            <Stack direction="row" spacing={1}>
                              {chunk.is_reference && (
                                <Chip size="small" label="ref" />
                              )}
                              {chunk.is_caption && (
                                <Chip size="small" label="caption" />
                              )}
                              {chunk.is_table && (
                                <Chip size="small" label="table" />
                              )}
                              {chunk.is_figure && (
                                <Chip size="small" label="figure" />
                              )}
                            </Stack>
                          </TableCell>
                          <TableCell>{chunk.token_count ?? "—"}</TableCell>
                          <TableCell>{chunk.text_preview}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              )}

              {tabIndex === 2 && (
                <Box sx={{ maxHeight: 360, overflow: "auto" }}>
                  <List dense>
                    {runs.map((run) => (
                      <ListItemButton
                        key={run.id}
                        selected={selectedRun === run.id}
                        onClick={() => handleSelectRun(run)}
                      >
                        <ListItemText
                          primary={run.input_query}
                          secondary={`${run.workflow_name} • ${run.status} • ${formatDate(run.started_at)}`}
                        />
                      </ListItemButton>
                    ))}
                  </List>
                  {runs.length === 0 && (
                    <Typography color="text.secondary">
                      No LangGraph runs recorded for this document yet.
                    </Typography>
                  )}
                </Box>
              )}

              {tabIndex === 3 && (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Node</TableCell>
                      <TableCell>Type</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Latency (ms)</TableCell>
                      <TableCell>Error</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {nodes.map((node) => (
                      <TableRow key={node.id}>
                        <TableCell>{node.node_key}</TableCell>
                        <TableCell>{node.node_type}</TableCell>
                        <TableCell>{node.status}</TableCell>
                        <TableCell>{node.latency_ms ?? "—"}</TableCell>
                        <TableCell>{node.error ?? ""}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Paper>
          )}
        </Box>
      </Box>
    </Box>
  );
}

export default PDFBrowserPage;
