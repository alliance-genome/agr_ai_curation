import { useState, useEffect } from 'react';
import {
  Box,
  TextField,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Chip,
  Paper,
} from '@mui/material';
import { Add, Delete } from '@mui/icons-material';
import axios from 'axios';

interface Entity {
  id: string;
  name: string;
  type: string;
  synonyms: string[];
  references: string[];
}

interface EntitiesTabProps {
  onEntityCountChange: (count: number) => void;
}

function EntitiesTab({ onEntityCountChange }: EntitiesTabProps) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [name, setName] = useState('');
  const [type, setType] = useState('gene');
  const [synonyms, setSynonyms] = useState('');
  const [references, setReferences] = useState('');

  useEffect(() => {
    fetchEntities();
  }, []);

  useEffect(() => {
    onEntityCountChange(entities.length);
  }, [entities, onEntityCountChange]);

  const fetchEntities = async () => {
    try {
      const response = await axios.get('/api/entities');
      setEntities(response.data);
    } catch (error) {
      console.error('Failed to fetch entities:', error);
    }
  };

  const handleAdd = async () => {
    if (!name.trim()) return;

    const newEntity = {
      name,
      type,
      synonyms: synonyms.split(',').map(s => s.trim()).filter(s => s),
      references: references.split(',').map(r => r.trim()).filter(r => r),
    };

    try {
      const response = await axios.post('/api/entities', newEntity);
      setEntities([...entities, response.data]);
      // Reset form
      setName('');
      setSynonyms('');
      setReferences('');
      setType('gene');
    } catch (error) {
      console.error('Failed to add entity:', error);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await axios.delete(`/api/entities/${id}`);
      setEntities(entities.filter(e => e.id !== id));
    } catch (error) {
      console.error('Failed to delete entity:', error);
    }
  };

  const getTypeColor = (type: string) => {
    const colors: Record<string, 'primary' | 'secondary' | 'success' | 'warning' | 'info'> = {
      gene: 'primary',
      protein: 'secondary',
      allele: 'success',
      strain: 'warning',
      phenotype: 'info',
    };
    return colors[type] || 'default';
  };

  return (
    <Box>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: '1fr 1fr' }}>
          <TextField
            fullWidth
            label="Entity Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            size="small"
          />
          
          <FormControl fullWidth size="small">
            <InputLabel>Type</InputLabel>
            <Select
              value={type}
              label="Type"
              onChange={(e) => setType(e.target.value)}
            >
              <MenuItem value="gene">Gene</MenuItem>
              <MenuItem value="protein">Protein</MenuItem>
              <MenuItem value="allele">Allele</MenuItem>
              <MenuItem value="strain">Strain</MenuItem>
              <MenuItem value="phenotype">Phenotype</MenuItem>
            </Select>
          </FormControl>

          <TextField
            fullWidth
            label="Synonyms (comma-separated)"
            value={synonyms}
            onChange={(e) => setSynonyms(e.target.value)}
            size="small"
          />
          
          <TextField
            fullWidth
            label="References (comma-separated)"
            value={references}
            onChange={(e) => setReferences(e.target.value)}
            size="small"
          />
        </Box>
        
        <Button
          variant="contained"
          startIcon={<Add />}
          onClick={handleAdd}
          sx={{ mt: 2 }}
          disabled={!name.trim()}
        >
          Add Entity
        </Button>
      </Paper>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Synonyms</TableCell>
              <TableCell>References</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {entities.map((entity) => (
              <TableRow key={entity.id}>
                <TableCell>{entity.name}</TableCell>
                <TableCell>
                  <Chip
                    label={entity.type}
                    size="small"
                    color={getTypeColor(entity.type)}
                  />
                </TableCell>
                <TableCell>{entity.synonyms.join(', ')}</TableCell>
                <TableCell>{entity.references.join(', ')}</TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    onClick={() => handleDelete(entity.id)}
                    color="error"
                  >
                    <Delete />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

export default EntitiesTab;