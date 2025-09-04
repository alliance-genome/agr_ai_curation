import { useState } from 'react';
import { 
  Box, 
  Paper, 
  Tabs, 
  Tab, 
  Typography,
  Badge,
} from '@mui/material';
import EntitiesTab from './tabs/EntitiesTab';
import AnnotationsTab from './tabs/AnnotationsTab';
import MetadataTab from './tabs/MetadataTab';
import ConfigTab from './tabs/ConfigTab';

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
        <Box sx={{ p: 2, height: '100%', overflow: 'auto' }}>
          {children}
        </Box>
      )}
    </div>
  );
}

function CurationPanel() {
  const [value, setValue] = useState(0);
  const [entityCount, setEntityCount] = useState(0);
  const [annotationCount] = useState(3); // Demo data

  const handleChange = (_event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue);
  };

  return (
    <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
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
              <Badge badgeContent={annotationCount} color="primary">
                <Typography>Annotations</Typography>
              </Badge>
            }
          />
          <Tab label="Metadata" />
          <Tab label="Config" />
        </Tabs>
      </Box>

      <Box sx={{ flexGrow: 1, overflow: 'hidden' }}>
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
          <ConfigTab />
        </TabPanel>
      </Box>
    </Paper>
  );
}

export default CurationPanel;