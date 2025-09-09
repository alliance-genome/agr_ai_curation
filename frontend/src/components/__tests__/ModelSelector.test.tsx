/**
 * Tests for ModelSelector component
 * Tests the AI model selection dropdown functionality
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import ModelSelector from "../ModelSelector";
import { act } from "react-dom/test-utils";

// Mock the API call for fetching models
global.fetch = jest.fn();

describe("ModelSelector Component", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("should render model selector with default selection", () => {
    const mockOnChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnChange}
        onModelChange={mockOnChange}
      />,
    );

    // Should display the current selection
    expect(screen.getByText(/openai/i)).toBeInTheDocument();
    expect(screen.getByText(/gpt-4o/i)).toBeInTheDocument();
  });

  it("should fetch and display available models on mount", async () => {
    const mockModels = {
      openai: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
      gemini: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    };

    (fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockModels,
    });

    const mockOnChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnChange}
        onModelChange={mockOnChange}
      />,
    );

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/chat/models");
    });
  });

  it("should handle provider change", async () => {
    const mockOnProviderChange = jest.fn();
    const mockOnModelChange = jest.fn();

    const { rerender } = render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnProviderChange}
        onModelChange={mockOnModelChange}
      />,
    );

    // Find and click the provider dropdown
    const providerSelect = screen.getByLabelText(/provider/i);
    fireEvent.mouseDown(providerSelect);

    // Select Gemini option
    const geminiOption = await screen.findByText("Gemini");
    fireEvent.click(geminiOption);

    expect(mockOnProviderChange).toHaveBeenCalledWith("gemini");
  });

  it("should handle model change within provider", async () => {
    const mockOnProviderChange = jest.fn();
    const mockOnModelChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnProviderChange}
        onModelChange={mockOnModelChange}
      />,
    );

    // Find and click the model dropdown
    const modelSelect = screen.getByLabelText(/model/i);
    fireEvent.mouseDown(modelSelect);

    // Select different model
    const miniOption = await screen.findByText("gpt-4o-mini");
    fireEvent.click(miniOption);

    expect(mockOnModelChange).toHaveBeenCalledWith("gpt-4o-mini");
  });

  it("should update model list when provider changes", async () => {
    const mockModels = {
      openai: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
      gemini: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    };

    (fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockModels,
    });

    const mockOnProviderChange = jest.fn();
    const mockOnModelChange = jest.fn();

    const { rerender } = render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnProviderChange}
        onModelChange={mockOnModelChange}
      />,
    );

    // Change to Gemini provider
    rerender(
      <ModelSelector
        selectedProvider="gemini"
        selectedModel="gemini-2.0-flash"
        onProviderChange={mockOnProviderChange}
        onModelChange={mockOnModelChange}
      />,
    );

    // Model dropdown should show Gemini models
    const modelSelect = screen.getByLabelText(/model/i);
    fireEvent.mouseDown(modelSelect);

    await waitFor(() => {
      expect(screen.getByText("gemini-2.0-flash")).toBeInTheDocument();
      expect(screen.getByText("gemini-1.5-pro")).toBeInTheDocument();
    });
  });

  it("should handle API error gracefully", async () => {
    (fetch as jest.Mock).mockRejectedValueOnce(new Error("API Error"));

    const mockOnChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnChange}
        onModelChange={mockOnChange}
      />,
    );

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/chat/models");
    });

    // Component should still render with defaults
    expect(screen.getByText(/openai/i)).toBeInTheDocument();
  });

  it("should disable selector when loading prop is true", () => {
    const mockOnChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnChange}
        onModelChange={mockOnChange}
        disabled={true}
      />,
    );

    const providerSelect = screen.getByLabelText(/provider/i);
    const modelSelect = screen.getByLabelText(/model/i);

    expect(providerSelect).toBeDisabled();
    expect(modelSelect).toBeDisabled();
  });

  it("should show loading state while fetching models", async () => {
    let resolvePromise: (value: any) => void;
    const promise = new Promise((resolve) => {
      resolvePromise = resolve;
    });

    (fetch as jest.Mock).mockReturnValue(promise);

    const mockOnChange = jest.fn();

    render(
      <ModelSelector
        selectedProvider="openai"
        selectedModel="gpt-4o"
        onProviderChange={mockOnChange}
        onModelChange={mockOnChange}
      />,
    );

    // Should show loading indicator
    expect(screen.getByTestId("model-selector-loading")).toBeInTheDocument();

    // Resolve the promise
    act(() => {
      resolvePromise!({
        ok: true,
        json: async () => ({
          openai: ["gpt-4o"],
          gemini: ["gemini-2.0-flash"],
        }),
      });
    });

    // Loading indicator should disappear
    await waitFor(() => {
      expect(
        screen.queryByTestId("model-selector-loading"),
      ).not.toBeInTheDocument();
    });
  });
});
