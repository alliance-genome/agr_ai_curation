import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import PDFUpload from "../PDFUpload";

describe("PDFUpload", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ pdf_id: "pdf-123" }),
      }) as unknown,
    );
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
      expect(onUploaded).toHaveBeenCalledWith("pdf-123", file),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("upload-success")).toBeInTheDocument();
  });
});
