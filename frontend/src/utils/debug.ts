// Global debug utility for the application
class DebugLogger {
  private enabled: boolean = false;
  private prefix: string = '[DEBUG]';

  constructor() {
    // Check localStorage on initialization
    this.enabled = localStorage.getItem('debug_mode') === 'true';
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    localStorage.setItem('debug_mode', enabled ? 'true' : 'false');
    
    if (enabled) {
      console.log('%c🔧 Debug Mode ENABLED', 'color: #4CAF50; font-weight: bold; font-size: 14px');
      console.log('%cAll debug messages will be displayed', 'color: #888; font-style: italic');
    } else {
      console.log('%c🔧 Debug Mode DISABLED', 'color: #f44336; font-weight: bold; font-size: 14px');
    }
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  // Main logging methods
  log(module: string, message: string, data?: any) {
    if (!this.enabled) return;
    
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    const moduleColor = this.getModuleColor(module);
    
    console.log(
      `%c${this.prefix} [${timestamp}] [${module}]%c ${message}`,
      `color: ${moduleColor}; font-weight: bold`,
      'color: inherit',
      data || ''
    );
  }

  error(module: string, message: string, error?: any) {
    if (!this.enabled) return;
    
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    
    console.error(
      `%c${this.prefix} ERROR [${timestamp}] [${module}]%c ${message}`,
      'color: #f44336; font-weight: bold',
      'color: #f44336',
      error || ''
    );
  }

  warn(module: string, message: string, data?: any) {
    if (!this.enabled) return;
    
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    
    console.warn(
      `%c${this.prefix} WARN [${timestamp}] [${module}]%c ${message}`,
      'color: #ff9800; font-weight: bold',
      'color: #ff9800',
      data || ''
    );
  }

  group(module: string, label: string) {
    if (!this.enabled) return;
    
    const moduleColor = this.getModuleColor(module);
    console.group(
      `%c${this.prefix} [${module}] ${label}`,
      `color: ${moduleColor}; font-weight: bold`
    );
  }

  groupEnd() {
    if (!this.enabled) return;
    console.groupEnd();
  }

  table(data: any) {
    if (!this.enabled) return;
    console.table(data);
  }

  // PDF-specific debug helpers
  pdfExtraction(message: string, data?: any) {
    this.log('PDF_EXTRACT', message, data);
  }

  pdfHighlight(message: string, data?: any) {
    this.log('PDF_HIGHLIGHT', message, data);
  }

  pdfRender(message: string, data?: any) {
    this.log('PDF_RENDER', message, data);
  }

  // Component-specific debug helpers
  chat(message: string, data?: any) {
    this.log('CHAT', message, data);
  }

  curation(message: string, data?: any) {
    this.log('CURATION', message, data);
  }

  api(message: string, data?: any) {
    this.log('API', message, data);
  }

  settings(message: string, data?: any) {
    this.log('SETTINGS', message, data);
  }

  // Performance debugging
  performance(label: string, callback: () => void) {
    if (!this.enabled) return callback();
    
    const startTime = performance.now();
    callback();
    const endTime = performance.now();
    
    this.log('PERFORMANCE', `${label} took ${(endTime - startTime).toFixed(2)}ms`);
  }

  async performanceAsync(label: string, callback: () => Promise<void>) {
    if (!this.enabled) return callback();
    
    const startTime = performance.now();
    await callback();
    const endTime = performance.now();
    
    this.log('PERFORMANCE', `${label} took ${(endTime - startTime).toFixed(2)}ms`);
  }

  // Helper to get consistent colors for modules
  private getModuleColor(module: string): string {
    const colors: { [key: string]: string } = {
      'PDF_EXTRACT': '#2196F3',
      'PDF_HIGHLIGHT': '#9C27B0',
      'PDF_RENDER': '#00BCD4',
      'CHAT': '#4CAF50',
      'CURATION': '#FF9800',
      'API': '#795548',
      'SETTINGS': '#607D8B',
      'PERFORMANCE': '#E91E63',
    };
    
    return colors[module] || '#666';
  }

  // Special method for highlighting debugging with visual output
  highlightDebug(searchTerm: string, textItems: any[], matches: any[]) {
    if (!this.enabled) return;
    
    console.group(`%c🔍 HIGHLIGHT DEBUG: "${searchTerm}"`, 'color: #9C27B0; font-weight: bold; font-size: 12px');
    
    console.log('%cSearch Term:', 'font-weight: bold', searchTerm);
    console.log('%cTotal Text Items:', 'font-weight: bold', textItems.length);
    console.log('%cMatches Found:', 'font-weight: bold', matches.length);
    
    if (matches.length > 0) {
      console.log('%c✅ Matches:', 'color: #4CAF50; font-weight: bold');
      matches.forEach((match, index) => {
        console.log(`  Match ${index + 1}:`, {
          text: match.text,
          position: match.position,
          page: match.page,
        });
      });
    } else {
      console.log('%c❌ No matches found', 'color: #f44336; font-weight: bold');
    }
    
    // Sample of text items for debugging
    console.log('%c📄 Sample Text Items (first 5):', 'color: #2196F3; font-weight: bold');
    textItems.slice(0, 5).forEach(item => {
      console.log('  ', {
        text: item.str,
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
      });
    });
    
    console.groupEnd();
  }
}

// Create and export a singleton instance
export const debug = new DebugLogger();

// Export for type usage
export type Debug = typeof debug;