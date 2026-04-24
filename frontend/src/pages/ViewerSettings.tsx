import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  Divider,
  FormControlLabel,
  Slider,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useTheme } from '@mui/material/styles'

import { dispatchHighlightSettingsChanged } from '@/components/pdfViewer/pdfEvents'
import {
  getDefaultHighlightSettings,
  loadStoredHighlightSettings,
  PDF_VIEWER_SETTINGS_STORAGE_KEY,
  type HighlightSettings,
} from '@/components/pdfViewer/highlightSettings'

function dispatchViewerSettings(settings: HighlightSettings) {
  dispatchHighlightSettingsChanged({
    color: settings.highlightColor,
    opacity: settings.highlightOpacity,
    clearOnNewQuery: settings.clearOnNewQuery,
  })
}

export default function ViewerSettings() {
  const theme = useTheme()
  const defaultSettings = useMemo(() => getDefaultHighlightSettings(theme), [theme])
  const [settings, setSettings] = useState<HighlightSettings>(() => defaultSettings)

  const applySettings = useCallback((next: HighlightSettings | ((prev: HighlightSettings) => HighlightSettings)) => {
    setSettings((prev) => {
      const resolved = typeof next === 'function' ? (next as (prev: HighlightSettings) => HighlightSettings)(prev) : next
      dispatchViewerSettings(resolved)
      return resolved
    })
  }, [])

  useEffect(() => {
    applySettings(loadStoredHighlightSettings(defaultSettings))
  }, [applySettings, defaultSettings])

  useEffect(() => {
    localStorage.setItem(PDF_VIEWER_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  }, [settings])

  const opacityLabel = useMemo(() => `${Math.round(settings.highlightOpacity * 100)}%`, [settings.highlightOpacity])

  return (
    <Box sx={{ padding: 3, display: 'flex', justifyContent: 'center', width: '100%' }}>
      <Card sx={{ maxWidth: 640, width: '100%' }}>
        <CardContent>
          <Stack spacing={3}>
            <Box>
              <Typography variant="h5" gutterBottom>
                PDF Viewer Preferences
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Tune highlight styles and default viewer behavior. Updates are dispatched immediately to the active
                viewer session and stored locally for future visits.
              </Typography>
            </Box>

            <Divider textAlign="left">Highlight Style</Divider>

            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center">
              <TextField
                type="color"
                label="Highlight Color"
                value={settings.highlightColor}
                onChange={(event) => applySettings((prev) => ({ ...prev, highlightColor: event.target.value }))}
                InputLabelProps={{ shrink: true }}
                sx={{ width: 160 }}
              />

              <Box sx={{ flexGrow: 1 }}>
                <Typography gutterBottom>Highlight Opacity ({opacityLabel})</Typography>
                <Slider
                  value={settings.highlightOpacity}
                  min={0.1}
                  max={1}
                  step={0.05}
                  onChange={(_, value) => applySettings((prev) => ({ ...prev, highlightOpacity: value as number }))}
                />
              </Box>
            </Stack>

            <Divider textAlign="left">Automation</Divider>

            <FormControlLabel
              control={
                <Switch
                  checked={settings.clearOnNewQuery}
                  onChange={(_, checked) => applySettings((prev) => ({ ...prev, clearOnNewQuery: checked }))}
                />
              }
              label="Automatically clear highlights when a new chat query starts"
            />

            <Divider textAlign="left">Session Controls</Divider>

            <Typography variant="body2" color="text.secondary">
              These settings work alongside viewer session persistence implemented later in the feature. You can safely
              reset everything to the defaults if you need a clean slate.
            </Typography>

            <Box>
              <Button
                variant="outlined"
                onClick={() => applySettings(defaultSettings)}
              >
                Restore Defaults
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  )
}
