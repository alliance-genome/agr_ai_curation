export interface TextItem {
  str: string;
  transform: number[];
  width: number;
  height: number;
  x: number;
  y: number;
}

export interface PageTextContent {
  pageNumber: number;
  items: TextItem[];
  text: string; // Full text of the page concatenated
}

export interface PdfTextData {
  pages: PageTextContent[];
  fullText: string; // All pages concatenated
  totalPages: number;
}