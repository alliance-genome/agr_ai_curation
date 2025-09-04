import { debug } from './debug';

export interface TextItem {
  str: string;
  transform: number[];
  width: number;
  height: number;
  dir: string;
  fontName: string;
}

export interface HighlightMatch {
  text: string;
  pageNumber: number;
  itemIndex: number;
  charIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Calculate the approximate width of a substring within a text item
 * This uses a simple character-based approximation
 */
function calculateSubstringWidth(
  fullText: string,
  substringStart: number,
  substringLength: number,
  totalWidth: number
): { x: number; width: number } {
  if (!fullText || fullText.length === 0) {
    return { x: 0, width: 0 };
  }

  // Simple approximation: assume uniform character width
  const charWidth = totalWidth / fullText.length;
  
  return {
    x: charWidth * substringStart,
    width: charWidth * substringLength
  };
}

/**
 * Find all matches of search terms in PDF text items
 * Returns precise positions for highlighting only the matched text
 */
export function findHighlightMatches(
  textItems: TextItem[],
  searchTerms: string[],
  viewport: any,
  pageNumber: number
): HighlightMatch[] {
  const matches: HighlightMatch[] = [];
  
  if (!searchTerms || searchTerms.length === 0) return matches;
  
  debug.group('PDF_HIGHLIGHT', 'Finding precise highlight matches');
  debug.pdfHighlight(`Search terms: ${searchTerms.join(', ')}`);
  debug.pdfHighlight(`Processing ${textItems.length} text items`);
  
  searchTerms.forEach(term => {
    const searchTerm = term.toLowerCase().trim();
    if (!searchTerm) return;
    
    debug.pdfHighlight(`🔍 Searching for: "${searchTerm}"`);
    let termMatchCount = 0;
    
    textItems.forEach((item, itemIndex) => {
      const itemText = item.str.toLowerCase();
      
      // Find all occurrences of the search term in this text item
      let searchIndex = 0;
      while ((searchIndex = itemText.indexOf(searchTerm, searchIndex)) !== -1) {
        // Calculate the position and width of JUST the matched substring
        const { x: relativeX, width: matchWidth } = calculateSubstringWidth(
          item.str,
          searchIndex,
          searchTerm.length,
          item.width
        );
        
        // Get the base position of the text item
        const itemX = item.transform[4];
        const itemY = item.transform[5];
        
        // Calculate the actual position of the match
        const match: HighlightMatch = {
          text: item.str.substring(searchIndex, searchIndex + searchTerm.length),
          pageNumber,
          itemIndex,
          charIndex: searchIndex,
          x: itemX + relativeX,
          y: viewport.height - itemY - item.height, // Convert to screen coordinates
          width: matchWidth,
          height: item.height
        };
        
        matches.push(match);
        termMatchCount++;
        
        if (termMatchCount <= 3) { // Log first few matches for debugging
          debug.pdfHighlight(`  ✅ Match ${termMatchCount} in item ${itemIndex}: "${item.str}"`);
          debug.pdfHighlight(`     Substring: "${match.text}" at char index ${searchIndex}`);
          debug.pdfHighlight(`     Position: (${match.x.toFixed(2)}, ${match.y.toFixed(2)})`);
          debug.pdfHighlight(`     Size: ${match.width.toFixed(2)}x${match.height.toFixed(2)}`);
        }
        
        searchIndex += searchTerm.length;
      }
    });
    
    debug.pdfHighlight(`  Found ${termMatchCount} matches for "${searchTerm}"`);
  });
  
  debug.pdfHighlight(`📊 Total matches: ${matches.length}`);
  debug.groupEnd();
  
  return matches;
}

/**
 * Create highlight overlays for matches
 */
export function createHighlightOverlays(
  matches: HighlightMatch[],
  container: HTMLElement
) {
  debug.group('PDF_HIGHLIGHT', 'Creating highlight overlays');
  
  // Clear existing highlights
  container.innerHTML = '';
  
  matches.forEach((match, index) => {
    const highlightDiv = document.createElement('div');
    highlightDiv.className = 'pdf-highlight';
    highlightDiv.style.position = 'absolute';
    highlightDiv.style.left = `${match.x}px`;
    highlightDiv.style.top = `${match.y}px`;
    highlightDiv.style.width = `${match.width}px`;
    highlightDiv.style.height = `${match.height}px`;
    highlightDiv.style.backgroundColor = 'rgba(255, 235, 59, 0.4)';
    highlightDiv.style.mixBlendMode = 'multiply';
    highlightDiv.style.pointerEvents = 'none';
    highlightDiv.style.borderRadius = '2px';
    highlightDiv.setAttribute('data-highlight-text', match.text);
    highlightDiv.setAttribute('data-highlight-index', index.toString());
    
    container.appendChild(highlightDiv);
    
    if (index < 5) { // Log first few highlights
      debug.pdfHighlight(`  Created highlight ${index + 1}: "${match.text}" at (${match.x.toFixed(2)}, ${match.y.toFixed(2)})`);
    }
  });
  
  debug.pdfHighlight(`✅ Created ${matches.length} highlight overlays`);
  debug.groupEnd();
}