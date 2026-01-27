import React from 'react';
import {
  Box,
  Paper,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Button,
} from '@mui/material';

const SchemaSettings: React.FC = () => {
  // Mock schema data
  const schemaProperties = [
    { name: 'filename', type: 'string', indexed: true },
    { name: 'content', type: 'text', indexed: false },
    { name: 'embedding', type: 'vector', indexed: true },
    { name: 'chunk_index', type: 'int', indexed: true },
    { name: 'created_at', type: 'date', indexed: true },
  ];

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Schema Settings
      </Typography>

      <Paper sx={{ p: 3, mt: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6">
            Document Schema
          </Typography>
          <Button variant="outlined" size="small">
            Export Schema
          </Button>
        </Box>

        <TableContainer>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Property</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Indexed</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {schemaProperties.map((prop) => (
                <TableRow key={prop.name}>
                  <TableCell>{prop.name}</TableCell>
                  <TableCell>
                    <Chip
                      label={prop.type}
                      size="small"
                      variant="outlined"
                      color={prop.type === 'vector' ? 'primary' : 'default'}
                    />
                  </TableCell>
                  <TableCell>
                    {prop.indexed ? (
                      <Chip label="Yes" size="small" color="success" />
                    ) : (
                      <Chip label="No" size="small" variant="outlined" />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

        <Box sx={{ mt: 3 }}>
          <Typography variant="body2" color="text.secondary">
            The schema defines the structure of documents stored in Weaviate.
            Modifying the schema requires reindexing all documents.
          </Typography>
        </Box>
      </Paper>
    </Box>
  );
};

export default SchemaSettings;