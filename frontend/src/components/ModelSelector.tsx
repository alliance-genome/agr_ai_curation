import React, { useState, useEffect } from "react";
import { ChevronDown } from "lucide-react";

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
  className = "",
}) => {
  const [models, setModels] = useState<Model[]>([]);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [isOpen, setIsOpen] = useState(false);
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

  const handleModelSelect = (model: Model) => {
    setSelectedModel(model);
    onModelChange(model.provider, model.id);
    setIsOpen(false);
  };

  const getProviderColor = (provider: string): string => {
    return provider === "openai" ? "text-green-600" : "text-blue-600";
  };

  if (loading) {
    return (
      <div
        className={`inline-flex items-center px-3 py-2 text-sm text-gray-500 ${className}`}
      >
        Loading models...
      </div>
    );
  }

  return (
    <div className={`relative inline-block text-left ${className}`}>
      <button
        type="button"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        className={`
          inline-flex items-center justify-between w-full px-3 py-2 text-sm
          bg-white border border-gray-300 rounded-md shadow-sm
          ${disabled ? "cursor-not-allowed opacity-50" : "hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"}
        `}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <div className="flex items-center">
          {selectedModel && (
            <>
              <span
                className={`mr-2 font-medium ${getProviderColor(selectedModel.provider)}`}
              >
                {selectedModel.provider === "openai" ? "OpenAI" : "Gemini"}
              </span>
              <span className="text-gray-700">{selectedModel.name}</span>
            </>
          )}
        </div>
        <ChevronDown
          className={`ml-2 h-4 w-4 transition-transform ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      {isOpen && (
        <div className="absolute z-10 mt-1 w-full bg-white shadow-lg max-h-60 rounded-md py-1 text-base ring-1 ring-black ring-opacity-5 overflow-auto focus:outline-none sm:text-sm">
          {models.map((model) => (
            <button
              key={`${model.provider}-${model.id}`}
              onClick={() => handleModelSelect(model)}
              className={`
                w-full text-left px-3 py-2 hover:bg-gray-100
                ${selectedModel?.id === model.id && selectedModel?.provider === model.provider ? "bg-gray-50" : ""}
              `}
            >
              <div className="flex items-center justify-between">
                <div>
                  <span
                    className={`font-medium ${getProviderColor(model.provider)}`}
                  >
                    {model.provider === "openai" ? "OpenAI" : "Gemini"}
                  </span>
                  <span className="ml-2 text-gray-900">{model.name}</span>
                </div>
                {model.description && (
                  <span className="text-xs text-gray-500">
                    {model.description}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default ModelSelector;
