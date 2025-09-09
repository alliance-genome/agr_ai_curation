import "@testing-library/jest-dom";
import { vi } from "vitest";

// Mock fetch for tests
global.fetch = vi.fn();

// Mock EventSource for SSE tests
global.EventSource = vi.fn() as any;

// Mock scrollIntoView which doesn't exist in jsdom
Element.prototype.scrollIntoView = vi.fn();

// Mock other missing DOM methods
if (!HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = vi.fn();
}

// Mock IntersectionObserver
global.IntersectionObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));
