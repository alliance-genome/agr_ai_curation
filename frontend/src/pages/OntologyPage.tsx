import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AppBar,
  Toolbar,
  Typography,
  IconButton,
  Button,
  Box,
  Container,
  Paper,
  CircularProgress,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Chip,
  Stack,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
  Snackbar,
} from "@mui/material";
import {
  Home as HomeIcon,
  Settings as SettingsIcon,
  Description,
  AdminPanelSettings as AdminIcon,
  AccountTree,
  Refresh,
  Brightness4,
  Brightness7,
} from "@mui/icons-material";
import axios from "axios";
import { useTheme } from "@mui/material/styles";

import {
  OntologyEmbeddingResponse,
  OntologyIngestionResponse,
  OntologyStatus,
} from "../types/ontology";

interface SnackbarState {
  open: boolean;
  message: string;
  severity: "success" | "error" | "info";
}

interface OntologyPageProps {
  toggleColorMode: () => void;
}

function OntologyPage({ toggleColorMode }: OntologyPageProps) {
  const navigate = useNavigate();
  const theme = useTheme();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: "",
    severity: "info",
  });
  const [statuses, setStatuses] = useState<OntologyStatus[]>([]);
  const [selectedStatus, setSelectedStatus] = useState<OntologyStatus | null>(
    null,
  );
  const [dialogOpen, setDialogOpen] = useState(false);
  const [oboPathOverride, setOboPathOverride] = useState<string>("");
  const [triggeringKey, setTriggeringKey] = useState<string | null>(null);
  const [embeddingKey, setEmbeddingKey] = useState<string | null>(null);

  const keyForStatus = useCallback(
    (status: OntologyStatus) => `${status.ontology_type}:${status.source_id}`,
    [],
  );

  const fetchStatuses = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get<OntologyStatus[]>(
        "/api/ontology/ingestions",
      );
      const nextStatuses = response.data ?? [];
      setStatuses(nextStatuses);
      if (dialogOpen && selectedStatus) {
        const updated = nextStatuses.find(
          (status) => keyForStatus(status) === keyForStatus(selectedStatus),
        );
        if (updated) {
          setSelectedStatus(updated);
        }
      }
    } catch (err) {
      console.error("Failed to load ontology ingestions", err);
      setError("Unable to load ontology ingestion status");
    } finally {
      setLoading(false);
    }
  }, [dialogOpen, selectedStatus, keyForStatus]);

  const closeSnackbar = () => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  const openDetails = (status: OntologyStatus) => {
    setSelectedStatus(status);
    setOboPathOverride("");
    setDialogOpen(true);
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setSelectedStatus(null);
    setOboPathOverride("");
  };

  const stateChipColor = (state: OntologyStatus["state"]) => {
    switch (state) {
      case "ready":
        return "success";
      case "indexing":
        return "info";
      case "error":
        return "error";
      default:
        return "default";
    }
  };

  const extractStage = (message: OntologyStatus["message"]) => {
    if (!message) {
      return "—";
    }
    if (typeof message === "string") {
      return message;
    }
    if (Array.isArray(message)) {
      return "details";
    }
    const stage = message.stage;
    if (typeof stage === "string" && stage.trim().length > 0) {
      switch (stage) {
        case "embedding_running":
          return "Embedding in progress";
        case "awaiting_embeddings":
          return "Awaiting embeddings";
        case "indexing":
          return "Indexing";
        case "ready":
          return "Ready";
        case "error":
          return "Error";
        default:
          return stage;
      }
    }
    return "details";
  };

  const extractEmbeddingMeta = (
    message: OntologyStatus["message"],
  ): { text: string; showSpinner: boolean } | null => {
    if (!message || typeof message === "string" || Array.isArray(message)) {
      return null;
    }

    const stage = message.stage;
    const embeddingValue = message.embedding;

    if (stage === "embedding_running") {
      if (
        embeddingValue &&
        typeof embeddingValue === "object" &&
        "model" in embeddingValue
      ) {
        const info = embeddingValue as Record<string, unknown>;
        const model = typeof info.model === "string" ? info.model : "model";
        return {
          text: `Embedding started with ${model}. Use Refresh to check progress.`,
          showSpinner: true,
        };
      }
      return {
        text: "Embedding job is running. Refresh later to see updated counts.",
        showSpinner: true,
      };
    }

    if (stage === "awaiting_embeddings") {
      return {
        text: "Embeddings not generated yet. Click Run Embeddings to create them.",
        showSpinner: false,
      };
    }

    if (embeddingValue && typeof embeddingValue === "object") {
      const info = embeddingValue as Record<string, unknown>;
      const embedded = info.embedded;
      const skipped = info.skipped;
      const model = info.model;
      if (typeof embedded === "number") {
        return {
          text: `Last embedding run: ${embedded} chunks embedded, ${
            typeof skipped === "number" ? skipped : 0
          } skipped (${typeof model === "string" ? model : "model"}).`,
          showSpinner: false,
        };
      }
      if (typeof info.error === "string") {
        return {
          text: `Embedding error: ${info.error}`,
          showSpinner: false,
        };
      }
    }

    return null;
  };

  const formatDate = (value?: string | null) => {
    if (!value) {
      return "—";
    }
    try {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return value;
      }
      return date.toLocaleString();
    } catch {
      return value;
    }
  };

  const triggerIngestion = async (
    status: OntologyStatus,
    options?: { oboPath?: string },
  ) => {
    const key = keyForStatus(status);
    setTriggeringKey(key);
    try {
      const payload = {
        ontology_type: status.ontology_type,
        source_id: status.source_id,
        ...(options?.oboPath ? { obo_path: options.oboPath } : {}),
      };
      const response = await axios.post<OntologyIngestionResponse>(
        "/api/ontology/ingestions",
        payload,
      );
      const summary = response.data?.summary;
      const embedded = summary?.embedded ?? 0;
      const message =
        embedded > 0
          ? `Reindexed ${status.ontology_type} (${status.source_id}); embedded ${embedded} chunks.`
          : `Reindexed ${status.ontology_type} (${status.source_id}); embeddings pending.`;
      setSnackbar({
        open: true,
        severity: "success",
        message,
      });
      await fetchStatuses();
    } catch (err) {
      console.error("Failed to trigger ontology ingestion", err);
      setSnackbar({
        open: true,
        severity: "error",
        message: "Failed to trigger ontology ingestion",
      });
    } finally {
      setTriggeringKey(null);
    }
  };

  const runEmbeddings = async (status: OntologyStatus) => {
    const key = keyForStatus(status);
    setEmbeddingKey(key);
    try {
      const response = await axios.post<OntologyEmbeddingResponse>(
        `/api/ontology/ingestions/${status.ontology_type}/${status.source_id}/embeddings`,
      );
      const summary = response.data?.summary;
      const model = summary?.model;
      setSnackbar({
        open: true,
        severity: "info",
        message: model
          ? `Embedding job started with ${model}. Press Refresh to see progress.`
          : "Embedding job queued. Refresh periodically to monitor progress.",
      });
      // Give the background job a moment before we refresh status
      window.setTimeout(() => {
        void fetchStatuses();
      }, 5000);
    } catch (err) {
      console.error("Failed to run ontology embeddings", err);
      setSnackbar({
        open: true,
        severity: "error",
        message: "Failed to regenerate ontology embeddings",
      });
    } finally {
      setEmbeddingKey(null);
    }
  };

  const handleDialogReindex = async () => {
    if (!selectedStatus) {
      return;
    }
    await triggerIngestion(selectedStatus, {
      oboPath: oboPathOverride.trim() || undefined,
    });
    closeDialog();
  };

  const stagedStatuses = useMemo(() => statuses, [statuses]);

  const handleSeedIngestion = async () => {
    const placeholder: OntologyStatus = {
      ontology_type: "disease",
      source_id: "all",
      state: "not_indexed",
      created_at: null,
      updated_at: null,
      message: null,
      term_count: 0,
      relation_count: 0,
      chunk_count: 0,
    };
    await triggerIngestion(placeholder);
  };

  const messageText = useMemo(() => {
    if (!selectedStatus || !selectedStatus.message) {
      return "";
    }
    if (typeof selectedStatus.message === "string") {
      return selectedStatus.message;
    }
    try {
      return JSON.stringify(selectedStatus.message, null, 2);
    } catch (err) {
      console.warn("Failed to stringify ontology status message", err);
      return String(selectedStatus.message);
    }
  }, [selectedStatus]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <AppBar position="static">
        <Toolbar>
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, cursor: "pointer" }}
            onClick={() => navigate("/")}
          >
            Ontology Management
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
            startIcon={<AccountTree />}
            onClick={() => navigate("/ontology")}
            sx={{ mr: 1 }}
          >
            Ontologies
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

      <Container sx={{ flexGrow: 1, py: 4 }} maxWidth="lg">
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Typography variant="h4">Ontology Sources</Typography>
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              startIcon={<Refresh />}
              onClick={() => void fetchStatuses()}
              disabled={loading}
            >
              Refresh
            </Button>
          </Stack>
        </Stack>

        <Paper>
          {loading ? (
            <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}>
              <CircularProgress />
            </Box>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Ontology</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Stage</TableCell>
                  <TableCell align="right">Terms</TableCell>
                  <TableCell align="right">Relations</TableCell>
                  <TableCell align="right">Chunks</TableCell>
                  <TableCell align="right">Embedded</TableCell>
                  <TableCell>Updated</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {stagedStatuses.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={10}>
                      <Box
                        sx={{
                          py: 6,
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          gap: 2,
                        }}
                      >
                        <Typography align="center" sx={{ maxWidth: 360 }}>
                          {error
                            ? `${error}. Use Refresh to retry.`
                            : "No ontology data loaded yet. Click Refresh to fetch status."}
                        </Typography>
                        {!error ? (
                          <Button
                            variant="contained"
                            startIcon={<AccountTree />}
                            onClick={() => void handleSeedIngestion()}
                            disabled={
                              triggeringKey !== null || embeddingKey !== null
                            }
                          >
                            Load Disease Ontology
                          </Button>
                        ) : null}
                      </Box>
                    </TableCell>
                  </TableRow>
                ) : (
                  stagedStatuses.map((status) => {
                    const key = keyForStatus(status);
                    return (
                      <TableRow
                        key={key}
                        hover
                        sx={{ cursor: "pointer" }}
                        onClick={() => openDetails(status)}
                      >
                        <TableCell>{status.ontology_type}</TableCell>
                        <TableCell>{status.source_id}</TableCell>
                        <TableCell>
                          <Chip
                            label={status.state}
                            color={stateChipColor(status.state)}
                            size="small"
                          />
                        </TableCell>
                        <TableCell>
                          <Stack spacing={1}>
                            <Stack
                              direction="row"
                              spacing={1}
                              alignItems="center"
                            >
                              <Typography variant="body2">
                                {extractStage(status.message)}
                              </Typography>
                              {extractEmbeddingMeta(status.message)
                                ?.showSpinner ? (
                                <CircularProgress size={14} thickness={6} />
                              ) : null}
                            </Stack>
                            {(() => {
                              const meta = extractEmbeddingMeta(status.message);
                              if (!meta) {
                                return null;
                              }
                              return (
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{ maxWidth: 260 }}
                                >
                                  {meta.text}
                                </Typography>
                              );
                            })()}
                          </Stack>
                        </TableCell>
                        <TableCell align="right">{status.term_count}</TableCell>
                        <TableCell align="right">
                          {status.relation_count}
                        </TableCell>
                        <TableCell align="right">
                          {status.chunk_count}
                        </TableCell>
                        <TableCell align="right">
                          {status.embedded_count ?? "—"}
                        </TableCell>
                        <TableCell>{formatDate(status.updated_at)}</TableCell>
                        <TableCell align="right">
                          <Stack
                            direction="row"
                            spacing={1}
                            justifyContent="flex-end"
                          >
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={(event) => {
                                event.stopPropagation();
                                void triggerIngestion(status);
                              }}
                              disabled={
                                triggeringKey === key || embeddingKey === key
                              }
                            >
                              {triggeringKey === key
                                ? "Reindexing…"
                                : "Reindex"}
                            </Button>
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={(event) => {
                                event.stopPropagation();
                                void runEmbeddings(status);
                              }}
                              disabled={embeddingKey === key}
                            >
                              {embeddingKey === key
                                ? "Embedding…"
                                : "Run Embeddings"}
                            </Button>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          )}
        </Paper>
      </Container>

      <Dialog open={dialogOpen} onClose={closeDialog} fullWidth maxWidth="md">
        <DialogTitle>Ontology Details</DialogTitle>
        <DialogContent dividers>
          {selectedStatus ? (
            <Stack spacing={2}>
              <Stack direction="row" spacing={2}>
                <TextField
                  label="Ontology"
                  value={selectedStatus.ontology_type}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
                <TextField
                  label="Source ID"
                  value={selectedStatus.source_id}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
              </Stack>
              <Stack direction="row" spacing={2}>
                <TextField
                  label="State"
                  value={selectedStatus.state}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
                <TextField
                  label="Updated"
                  value={formatDate(selectedStatus.updated_at)}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
              </Stack>
              <Stack direction="row" spacing={2}>
                <TextField
                  label="Terms"
                  value={selectedStatus.term_count}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
                <TextField
                  label="Relations"
                  value={selectedStatus.relation_count}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
                <TextField
                  label="Chunks"
                  value={selectedStatus.chunk_count}
                  InputProps={{ readOnly: true }}
                  fullWidth
                />
              </Stack>
              <TextField
                label="Message"
                value={messageText}
                InputProps={{ readOnly: true }}
                multiline
                minRows={6}
              />
              <TextField
                label="Custom OBO path (optional)"
                value={oboPathOverride}
                onChange={(event) => setOboPathOverride(event.target.value)}
                placeholder="/data/ontologies/doid.obo"
                fullWidth
              />
            </Stack>
          ) : null}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog}>Close</Button>
          <Button
            variant="contained"
            onClick={() => void handleDialogReindex()}
            disabled={triggeringKey !== null}
          >
            Reindex
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={closeSnackbar}
      >
        <Alert
          onClose={closeSnackbar}
          severity={snackbar.severity}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

export default OntologyPage;
