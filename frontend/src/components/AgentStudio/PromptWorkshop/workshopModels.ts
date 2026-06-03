// Models offered to curators in the Workshop. Keep in sync with the models
// actually wired in packages/core/config/models.yaml. We intentionally hide
// experimental/unused entries (e.g. gpt-oss-120b) from the curator-facing picker.
export const WORKSHOP_MODEL_IDS = ['gpt-5.5', 'gpt-5-mini'] as const

export function isWorkshopModel(modelId: string): boolean {
  return (WORKSHOP_MODEL_IDS as readonly string[]).includes(modelId)
}
