import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ModelSelector from "./ModelSelector";

describe("ModelSelector", () => {
  beforeEach(() => {
    // Reset fetch mock
    vi.clearAllMocks();
    (global.fetch as any).mockReset();
  });

  it("renders loading state initially", () => {
    (global.fetch as any).mockImplementation(() => new Promise(() => {}));

    render(<ModelSelector onModelChange={vi.fn()} />);

    // Should show loading text, not combobox yet
    expect(screen.getByText("Loading models...")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("fetches and displays models", async () => {
    const mockModels = {
      openai: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
      gemini: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    };

    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockModels,
    });

    const onModelChange = vi.fn();
    render(<ModelSelector onModelChange={onModelChange} />);

    // Wait for loading to finish and combobox to appear
    await waitFor(() => {
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    expect(global.fetch).toHaveBeenCalledWith("/api/agents/models");

    // Open the dropdown
    const select = screen.getByRole("combobox");
    fireEvent.mouseDown(select);

    await waitFor(() => {
      // Check that model names are in the dropdown menu items
      // Use getAllByText since they might appear multiple times (selected + in dropdown)
      const gpt4oElements = screen.getAllByText("GPT-4o");
      expect(gpt4oElements.length).toBeGreaterThan(0);

      const gpt4oMiniElements = screen.getAllByText("GPT-4o Mini");
      expect(gpt4oMiniElements.length).toBeGreaterThan(0);

      const geminiFlashElements = screen.getAllByText("Gemini 2.0 Flash");
      expect(geminiFlashElements.length).toBeGreaterThan(0);

      const geminiProElements = screen.getAllByText("Gemini 1.5 Pro");
      expect(geminiProElements.length).toBeGreaterThan(0);
    });
  });

  it("calls onModelChange when a model is selected", async () => {
    const mockModels = {
      openai: ["gpt-4o", "gpt-4o-mini"],
      gemini: ["gemini-2.0-flash"],
    };

    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockModels,
    });

    const onModelChange = vi.fn();
    render(<ModelSelector onModelChange={onModelChange} />);

    // Wait for loading to finish
    await waitFor(() => {
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    // The default model should have been selected
    expect(onModelChange).toHaveBeenCalledWith("openai", "gpt-4o");

    // Clear the mock to test the next selection
    onModelChange.mockClear();

    // Open dropdown and select a different model
    const select = screen.getByRole("combobox");
    fireEvent.mouseDown(select);

    await waitFor(() => {
      expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("GPT-4o Mini"));

    expect(onModelChange).toHaveBeenCalledWith("openai", "gpt-4o-mini");
  });

  it("handles fetch error gracefully", async () => {
    (global.fetch as any).mockRejectedValueOnce(new Error("Network error"));

    const onModelChange = vi.fn();
    render(<ModelSelector onModelChange={onModelChange} />);

    // Wait for the component to fall back to default models
    await waitFor(() => {
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    // Should have called onModelChange with the default fallback model
    expect(onModelChange).toHaveBeenCalledWith("openai", "gpt-4o");

    // Should still be able to open the dropdown with fallback models
    const select = screen.getByRole("combobox");
    fireEvent.mouseDown(select);

    await waitFor(() => {
      // Use getAllByText since model names appear multiple times
      const gpt4oElements = screen.getAllByText("GPT-4o");
      expect(gpt4oElements.length).toBeGreaterThan(0);

      const gpt4oMiniElements = screen.getAllByText("GPT-4o Mini");
      expect(gpt4oMiniElements.length).toBeGreaterThan(0);
    });
  });

  it("disables the selector when disabled prop is true", async () => {
    const mockModels = {
      openai: ["gpt-4o"],
      gemini: ["gemini-2.0-flash"],
    };

    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockModels,
    });

    render(<ModelSelector onModelChange={vi.fn()} disabled={true} />);

    // Wait for loading to finish
    await waitFor(() => {
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    const select = screen.getByRole("combobox");
    expect(select).toHaveAttribute("aria-disabled", "true");
  });
});
