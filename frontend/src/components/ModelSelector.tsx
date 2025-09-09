import React, { useState, useEffect } from "react";
import {
  FormControl,
  Select,
  MenuItem,
  SelectChangeEvent,
  ListItemText,
  ListItemIcon,
  Chip,
  Typography,
  Box,
  Divider,
} from "@mui/material";
import { AutoAwesome, Psychology } from "@mui/icons-material";

interface Model {
  id: string;
  name: string;
  provider: "openai" | "gemini";
  description?: string;
}

interface ModelSelectorProps {
  onModelChange: (provider: string, model: string) => void;
  disabled?: boolean;
  className?: string;
}

const ModelSelector: React.FC<ModelSelectorProps> = ({
  onModelChange,
  disabled = false,
}) => {
  const [models, setModels] = useState<Model[]>([]);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchModels();
  }, []);

  const fetchModels = async () => {
    try {
      const response = await fetch("/api/chat/models");
      const data = await response.json();

      const modelList: Model[] = [];

      // Process OpenAI models
      if (data.openai) {
        data.openai.forEach((modelId: string) => {
          modelList.push({
            id: modelId,
            name: getModelDisplayName(modelId),
            provider: "openai",
            description: getModelDescription(modelId),
          });
        });
      }

      // Process Gemini models
      if (data.gemini) {
        data.gemini.forEach((modelId: string) => {
          modelList.push({
            id: modelId,
            name: getModelDisplayName(modelId),
            provider: "gemini",
            description: getModelDescription(modelId),
          });
        });
      }

      setModels(modelList);

      // Set default model (first OpenAI model)
      if (modelList.length > 0) {
        const defaultModel =
          modelList.find((m) => m.provider === "openai") || modelList[0];
        setSelectedModel(defaultModel);
        onModelChange(defaultModel.provider, defaultModel.id);
      }
    } catch (error) {
      console.error("Failed to fetch models:", error);
      // Fallback to default models
      const fallbackModels: Model[] = [
        {
          id: "gpt-4o",
          name: "GPT-4o",
          provider: "openai",
          description: "Most capable OpenAI model",
        },
        {
          id: "gpt-4o-mini",
          name: "GPT-4o Mini",
          provider: "openai",
          description: "Faster, more affordable",
        },
        {
          id: "gemini-2.0-flash",
          name: "Gemini 2.0 Flash",
          provider: "gemini",
          description: "Fast Gemini model",
        },
      ];
      setModels(fallbackModels);
      setSelectedModel(fallbackModels[0]);
      onModelChange(fallbackModels[0].provider, fallbackModels[0].id);
    } finally {
      setLoading(false);
    }
  };

  const getModelDisplayName = (modelId: string): string => {
    const displayNames: { [key: string]: string } = {
      "gpt-4o": "GPT-4o",
      "gpt-4o-mini": "GPT-4o Mini",
      "gpt-3.5-turbo": "GPT-3.5 Turbo",
      "gemini-2.0-flash": "Gemini 2.0 Flash",
      "gemini-1.5-pro": "Gemini 1.5 Pro",
      "gemini-1.5-flash": "Gemini 1.5 Flash",
    };
    return displayNames[modelId] || modelId;
  };

  const getModelDescription = (modelId: string): string => {
    const descriptions: { [key: string]: string } = {
      "gpt-4o": "Most capable OpenAI model",
      "gpt-4o-mini": "Faster, more affordable",
      "gpt-3.5-turbo": "Legacy model, fast responses",
      "gemini-2.0-flash": "Latest fast Gemini model",
      "gemini-1.5-pro": "Advanced reasoning",
      "gemini-1.5-flash": "Quick responses",
    };
    return descriptions[modelId] || "";
  };

  const handleModelSelect = (event: SelectChangeEvent) => {
    const modelId = event.target.value;
    const model = models.find((m) => `${m.provider}-${m.id}` === modelId);
    if (model) {
      setSelectedModel(model);
      onModelChange(model.provider, model.id);
    }
  };

  const getProviderIcon = (provider: string) => {
    return provider === "openai" ? (
      <Psychology sx={{ fontSize: 20 }} />
    ) : (
      <AutoAwesome sx={{ fontSize: 20 }} />
    );
  };

  const getProviderColor = (provider: string) => {
    return provider === "openai" ? "success" : "info";
  };

  if (loading) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ px: 2 }}>
        Loading models...
      </Typography>
    );
  }

  const groupedModels = models.reduce(
    (acc, model) => {
      if (!acc[model.provider]) {
        acc[model.provider] = [];
      }
      acc[model.provider].push(model);
      return acc;
    },
    {} as Record<string, Model[]>,
  );

  return (
    <FormControl size="small" sx={{ minWidth: 280 }}>
      <Select
        value={
          selectedModel ? `${selectedModel.provider}-${selectedModel.id}` : ""
        }
        onChange={handleModelSelect}
        disabled={disabled}
        displayEmpty
        renderValue={(selected) => {
          if (!selected || !selectedModel) {
            return (
              <Typography color="text.secondary">Select a model</Typography>
            );
          }
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Chip
                icon={getProviderIcon(selectedModel.provider)}
                label={
                  selectedModel.provider === "openai" ? "OpenAI" : "Gemini"
                }
                size="small"
                color={getProviderColor(selectedModel.provider)}
                sx={{ height: 24 }}
              />
              <Typography variant="body2">{selectedModel.name}</Typography>
            </Box>
          );
        }}
        sx={{
          "& .MuiSelect-select": {
            py: 1,
          },
        }}
      >
        {Object.entries(groupedModels)
          .map(([provider, providerModels], index) => [
            index > 0 && <Divider key={`divider-${provider}`} />,
            <MenuItem key={`header-${provider}`} disabled>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600 }}
              >
                {provider === "openai" ? "OpenAI Models" : "Gemini Models"}
              </Typography>
            </MenuItem>,
            ...providerModels.map((model) => (
              <MenuItem
                key={`${model.provider}-${model.id}`}
                value={`${model.provider}-${model.id}`}
              >
                <ListItemIcon>{getProviderIcon(model.provider)}</ListItemIcon>
                <ListItemText
                  primary={model.name}
                  secondary={model.description}
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption" }}
                />
              </MenuItem>
            )),
          ])
          .flat()}
      </Select>
    </FormControl>
  );
};

export default ModelSelector;
