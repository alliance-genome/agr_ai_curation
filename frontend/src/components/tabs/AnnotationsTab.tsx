import { 
  Table, 
  TableBody, 
  TableCell, 
  TableContainer, 
  TableHead, 
  TableRow,
  Paper,
  Typography,
  Box,
} from '@mui/material';

// Demo data for annotations
const demoAnnotations = [
  {
    id: 1,
    subject: 'FBgn0000490',
    predicate: 'has_phenotype',
    object: 'wing development abnormal',
    evidence: 'IMP',
    reference: 'PMID:12345678',
  },
  {
    id: 2,
    subject: 'FBgn0000490',
    predicate: 'located_in',
    object: 'nucleus',
    evidence: 'IDA',
    reference: 'PMID:87654321',
  },
  {
    id: 3,
    subject: 'FBgn0001234',
    predicate: 'interacts_with',
    object: 'FBgn0005678',
    evidence: 'IPI',
    reference: 'PMID:11111111',
  },
];

function AnnotationsTab() {
  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Annotations will be extracted from the PDF and AI analysis. This tab shows demo data for now.
      </Typography>
      
      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Subject</TableCell>
              <TableCell>Predicate</TableCell>
              <TableCell>Object</TableCell>
              <TableCell>Evidence</TableCell>
              <TableCell>Reference</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {demoAnnotations.map((annotation) => (
              <TableRow key={annotation.id}>
                <TableCell>{annotation.subject}</TableCell>
                <TableCell>{annotation.predicate}</TableCell>
                <TableCell>{annotation.object}</TableCell>
                <TableCell>{annotation.evidence}</TableCell>
                <TableCell>{annotation.reference}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

export default AnnotationsTab;