import { useState } from "react";
import { Box, Paper, Tabs, Tab, Typography, Badge } from "@mui/material";
import EntitiesTab from "./tabs/EntitiesTab";
import AnnotationsTab from "./tabs/AnnotationsTab";
import MetadataTab from "./tabs/MetadataTab";
import TestTab from "./tabs/TestTab";
import { PdfTextData } from "../types/pdf";

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`curation-tabpanel-${index}`}
      aria-labelledby={`curation-tab-${index}`}
      {...other}
    >
      {value === index && (
        <Box sx={{ p: 2, height: "100%", overflow: "auto" }}>{children}</Box>
      )}
    </div>
  );
}

interface CurationPanelProps {
  onHighlight?: (searchTerm: string) => void;
  onClearHighlights?: () => void;
  pdfTextData?: PdfTextData | null;
}

function CurationPanel({ onHighlight, onClearHighlights }: CurationPanelProps) {
  const [value, setValue] = useState(0);
  const [entityCount, setEntityCount] = useState(0);
  const [annotationCount] = useState(3); // Demo data

  // Check localStorage for annotations viewed state
  const [annotationsViewed, setAnnotationsViewed] = useState(() => {
    return localStorage.getItem("annotationsViewed") === "true";
  });

  const handleChange = (_event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue);

    // Clear annotation count when the Annotations tab is clicked
    if (newValue === 1 && !annotationsViewed) {
      setAnnotationsViewed(true);
      // Save to localStorage so it persists across refreshes
      localStorage.setItem("annotationsViewed", "true");
    }
  };

  return (
    <Paper sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Tabs
          value={value}
          onChange={handleChange}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="curation tabs"
        >
          <Tab
            label={
              <Badge badgeContent={entityCount} color="primary">
                <Typography>Entities</Typography>
              </Badge>
            }
          />
          <Tab
            label={
              <Badge
                badgeContent={annotationsViewed ? 0 : annotationCount}
                color="primary"
              >
                <Typography>Annotations</Typography>
              </Badge>
            }
          />
          <Tab label="Metadata" />
          <Tab label="TEST" />
        </Tabs>
      </Box>

      <Box sx={{ flexGrow: 1, overflow: "hidden" }}>
        <TabPanel value={value} index={0}>
          <EntitiesTab onEntityCountChange={setEntityCount} />
        </TabPanel>
        <TabPanel value={value} index={1}>
          <AnnotationsTab />
        </TabPanel>
        <TabPanel value={value} index={2}>
          <MetadataTab />
        </TabPanel>
        <TabPanel value={value} index={3}>
          <TestTab
            onHighlight={onHighlight}
            onClearHighlights={onClearHighlights}
          />
        </TabPanel>
      </Box>
    </Paper>
  );
}

export default CurationPanel;
