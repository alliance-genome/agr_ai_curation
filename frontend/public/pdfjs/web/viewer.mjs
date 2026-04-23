import {
  AnnotationMode,
  GlobalWorkerOptions,
  getDocument,
} from "../build/pdf.mjs";
import {
  EventBus,
  PDFFindController,
  PDFLinkService,
  PDFViewer,
} from "./pdf_viewer.mjs";

const SUPPORTED_PROTOCOLS = new Set(["http:", "https:"]);
const GENERIC_LOAD_ERROR = "Unable to load the requested PDF document.";

const viewerContainer = document.getElementById("viewerContainer");
const viewer = document.getElementById("viewer");
const loadingNode = document.getElementById("viewerLoading");
const errorNode = document.getElementById("viewerError");

if (!(viewerContainer instanceof HTMLDivElement) || !(viewer instanceof HTMLDivElement)) {
  throw new Error("The PDF viewer shell did not render correctly.");
}

GlobalWorkerOptions.workerSrc = "../build/pdf.worker.mjs";

const eventBus = new EventBus();
const linkService = new PDFLinkService({ eventBus });
const findController = new PDFFindController({ eventBus, linkService });
const pdfViewer = new PDFViewer({
  container: viewerContainer,
  viewer,
  eventBus,
  linkService,
  findController,
  annotationMode: AnnotationMode.ENABLE_FORMS,
  imageResourcesPath: "./images/",
});

linkService.setViewer(pdfViewer);

const setLoadingState = (message = "") => {
  if (!loadingNode) {
    return;
  }

  if (message) {
    loadingNode.textContent = message;
    loadingNode.hidden = false;
    return;
  }

  loadingNode.hidden = true;
};

const setErrorState = (message = "") => {
  if (!errorNode) {
    return;
  }

  if (message) {
    errorNode.textContent = message;
    errorNode.hidden = false;
    return;
  }

  errorNode.textContent = "";
  errorNode.hidden = true;
};

const toSameOriginDocumentUrl = (input) => {
  if (typeof input !== "string" || input.trim().length === 0) {
    throw new Error("A PDF document URL is required.");
  }

  let resolvedUrl;
  try {
    resolvedUrl = new URL(input, window.location.origin);
  } catch (_error) {
    throw new Error("The PDF document URL is invalid.");
  }

  if (!SUPPORTED_PROTOCOLS.has(resolvedUrl.protocol)) {
    throw new Error("Unsupported PDF document URL protocol.");
  }

  if (resolvedUrl.origin !== window.location.origin) {
    throw new Error("The PDF viewer only supports same-origin document URLs.");
  }

  return resolvedUrl;
};

const PDFViewerApplication = {
  appConfig: {
    viewer,
    viewerContainer,
  },
  eventBus,
  findController,
  linkService,
  loadingTask: null,
  pdfDocument: null,
  pdfViewer,
  get page() {
    return this.linkService.page;
  },
  set page(value) {
    this.linkService.page = value;
  },
  async close() {
    setErrorState("");
    setLoadingState("");

    if (this.pdfDocument) {
      await this.pdfDocument.destroy();
      this.pdfDocument = null;
    } else if (this.loadingTask) {
      await this.loadingTask.destroy();
    }

    this.loadingTask = null;

    this.findController.setDocument(null);
    this.linkService.setDocument(null);
    this.pdfViewer.setDocument(null);
  },
  async open(inputUrl) {
    const documentUrl = toSameOriginDocumentUrl(inputUrl);

    await this.close();
    setErrorState("");
    setLoadingState("Loading PDF...");

    const loadingTask = getDocument({
      cMapPacked: true,
      cMapUrl: "../cmaps/",
      iccUrl: "../iccs/",
      standardFontDataUrl: "../standard_fonts/",
      url: documentUrl.toString(),
      wasmUrl: "../wasm/",
    });

    this.loadingTask = loadingTask;

    try {
      const pdfDocument = await loadingTask.promise;
      this.pdfDocument = pdfDocument;
      this.linkService.setDocument(pdfDocument, documentUrl.toString());
      this.pdfViewer.setDocument(pdfDocument);
      this.findController.setDocument(pdfDocument);
      this.eventBus.dispatch("documentloaded", { source: this });
      this.loadingTask = null;
      setLoadingState("");
      return pdfDocument;
    } catch (error) {
      this.loadingTask = null;
      this.pdfDocument = null;
      this.findController.setDocument(null);
      this.linkService.setDocument(null);
      this.pdfViewer.setDocument(null);
      console.error("Failed to load PDF document.", error);
      setLoadingState("");
      setErrorState(
        error instanceof Error && error.message ? error.message : GENERIC_LOAD_ERROR,
      );
      throw error;
    }
  },
};

eventBus.on("pagesinit", () => {
  pdfViewer.currentScaleValue = "auto";
});

window.PDFViewerApplication = PDFViewerApplication;

window.addEventListener("beforeunload", () => {
  void PDFViewerApplication.close();
});

const initialFile = new URLSearchParams(window.location.search).get("file");

if (initialFile) {
  void PDFViewerApplication.open(initialFile);
} else {
  setLoadingState("");
  setErrorState("No PDF document was specified.");
}
