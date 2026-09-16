import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Typography } from '@mui/material'
import { registerValidationReview, type ValidationCoverageScope } from '@/services/validationAcknowledgment'

export default function ValidationAcknowledgmentDialog() {
  const [scopes, setScopes] = useState<ValidationCoverageScope[] | null>(null)
  const [checked, setChecked] = useState(false)
  const pending = useRef<((accepted: boolean) => void) | null>(null)
  useEffect(() => {
    const unregister = registerValidationReview((next, signal) => {
      if (pending.current || signal?.aborted) return Promise.resolve(false)
      return new Promise<boolean>((resolve) => {
        const abort = () => finish(false)
        const finish = (accepted: boolean) => {
          signal?.removeEventListener('abort', abort)
          pending.current = null
          setScopes(null)
          setChecked(false)
          resolve(accepted)
        }
        pending.current = finish
        signal?.addEventListener('abort', abort, { once: true })
        setChecked(false)
        setScopes(next)
      })
    })
    return () => { unregister(); pending.current?.(false) }
  }, [])
  return <Dialog open={scopes !== null} onClose={() => pending.current?.(false)} maxWidth="sm" fullWidth aria-labelledby="validation-acknowledgment-title">
    <DialogTitle id="validation-acknowledgment-title">Continue without database validation?</DialogTitle>
    <DialogContent>
      <Alert severity="warning">These fields will contain paper-reported information without the configured database checks. Acknowledgment does not make results verified or ready for submission.</Alert>
      {scopes?.map((scope, index) => <section key={`${scope.configuration_fingerprint}:${index}`}>
        <Typography component="h3" sx={{ mt: 2 }}>{scope.data_type}</Typography>
        <ul>{scope.unvalidated_fields.map(field => <li key={field.path}>{field.label}</li>)}</ul>
      </section>)}
      <Typography>To configure validation, cancel and edit the agent’s validators or the flow step’s automatic checks.</Typography>
      <FormControlLabel sx={{ mt: 1 }} control={<Checkbox checked={checked} onChange={event => setChecked(event.target.checked)} />}
        label="I understand that these fields will not be database-validated and want to continue with extraction only." />
    </DialogContent>
    <DialogActions>
      <Button onClick={() => pending.current?.(false)} autoFocus>Cancel and configure validation</Button>
      <Button variant="contained" disabled={!checked} onClick={() => { if (checked) pending.current?.(true) }}>Continue without database validation</Button>
    </DialogActions>
  </Dialog>
}
