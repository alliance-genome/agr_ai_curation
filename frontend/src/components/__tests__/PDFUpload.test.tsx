import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import PDFUpload from "../PDFUpload";

describe("PDFUpload", () => {
  beforeEach(() => {
    const mockFetch = vi.fn(
      async (input: RequestInfo | URL): Promise<Response | any> => {
        const url = typeof input === "string" ? input : input.toString();

        if (url.endsWith("/api/pdf/upload")) {
          return {
            ok: true,
            json: async () => ({
              pdf_id: "pdf-123",
              filename: "test.pdf",
              reused: false,
            }),
          };
        }

        if (
          url.includes("/api/pdf-data/documents/") &&
          !url.endsWith("/embeddings")
        ) {
          return {
            ok: true,
            json: async () => ({
              id: "pdf-123",
              page_count: 12,
              chunk_count: 24,
              embeddings_generated: true,
            }),
          };
        }

        if (url.endsWith("/embeddings")) {
          return {
            ok: true,
            json: async () => [
              {
                model_name: "text-embedding-3-small",
                estimated_cost_usd: 0.00012,
              },
            ],
          };
        }

        return { ok: false, status: 404, json: async () => ({}) };
      },
    );

    vi.stubGlobal("fetch", mockFetch as unknown);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads a PDF file and notifies callback", async () => {
    const onUploaded = vi.fn();
    render(<PDFUpload onUploaded={onUploaded} />);

    const input = screen.getByTestId("pdf-input") as HTMLInputElement;
    const file = new File(["PDF"], "test.pdf", { type: "application/pdf" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(onUploaded).toHaveBeenCalledWith({
        pdfId: "pdf-123",
        filename: "test.pdf",
        viewerUrl: undefined,
      }),
    );

    await waitFor(() =>
      expect(screen.getByText(/processing complete/i)).toBeInTheDocument(),
    );
    expect(screen.getByTestId("upload-success")).toBeInTheDocument();
  });
});
