import { useEffect } from 'react'
import { debug } from '@/utils/env'

function ForceScrollFix() {
  useEffect(() => {
    // Force body and html to not scroll
    const forceNoScroll = () => {
      // Set on html and body
      document.documentElement.style.overflow = 'hidden';
      document.documentElement.style.height = '100%';
      document.body.style.overflow = 'hidden';
      document.body.style.height = '100%';
      document.body.style.margin = '0';
      document.body.style.padding = '0';

      // Find and log all scrollable elements
      const allElements = document.querySelectorAll('*');
      const scrollableElements: HTMLElement[] = [];

      allElements.forEach((el) => {
        const style = window.getComputedStyle(el);
        if (style.overflow === 'auto' || style.overflow === 'scroll' ||
            style.overflowY === 'auto' || style.overflowY === 'scroll') {
          scrollableElements.push(el as HTMLElement);
        }
      });

      debug.log('=== SCROLL DEBUG ===');
      debug.log('Found scrollable elements:', scrollableElements.length);
      scrollableElements.forEach((el, i) => {
        debug.log(`Scrollable #${i + 1}:`, {
          element: el,
          className: el.className,
          id: el.id,
          testId: el.getAttribute('data-testid'),
          overflow: window.getComputedStyle(el).overflow,
          overflowY: window.getComputedStyle(el).overflowY,
          height: window.getComputedStyle(el).height,
          maxHeight: window.getComputedStyle(el).maxHeight,
          scrollHeight: el.scrollHeight,
          clientHeight: el.clientHeight,
          isScrollable: el.scrollHeight > el.clientHeight
        });
      });

      // Check body scroll
      debug.log('Body scroll check:', {
        bodyScrollHeight: document.body.scrollHeight,
        bodyClientHeight: document.body.clientHeight,
        bodyOverflow: window.getComputedStyle(document.body).overflow,
        htmlScrollHeight: document.documentElement.scrollHeight,
        htmlClientHeight: document.documentElement.clientHeight,
        htmlOverflow: window.getComputedStyle(document.documentElement).overflow,
        windowInnerHeight: window.innerHeight,
        isBodyScrollable: document.body.scrollHeight > document.body.clientHeight,
        isHtmlScrollable: document.documentElement.scrollHeight > document.documentElement.clientHeight
      });

      // Find the messages container specifically
      const messagesContainer = document.querySelector('[data-testid="messages-container"]');
      if (messagesContainer) {
        debug.log('Messages container found:', {
          scrollHeight: messagesContainer.scrollHeight,
          clientHeight: messagesContainer.clientHeight,
          computedStyle: {
            overflow: window.getComputedStyle(messagesContainer).overflow,
            overflowY: window.getComputedStyle(messagesContainer).overflowY,
            height: window.getComputedStyle(messagesContainer).height,
            maxHeight: window.getComputedStyle(messagesContainer).maxHeight,
            flex: window.getComputedStyle(messagesContainer).flex,
            minHeight: window.getComputedStyle(messagesContainer).minHeight,
          }
        });
      } else {
        debug.log('Messages container NOT found!');
      }

      // Find #root
      const root = document.getElementById('root');
      if (root) {
        debug.log('Root element:', {
          scrollHeight: root.scrollHeight,
          clientHeight: root.clientHeight,
          overflow: window.getComputedStyle(root).overflow,
          height: window.getComputedStyle(root).height,
        });
      }
    };

    // Run immediately
    forceNoScroll();

    // Run again after a delay to catch any late changes
    setTimeout(forceNoScroll, 1000);
    setTimeout(forceNoScroll, 3000);

    // Add resize listener
    window.addEventListener('resize', forceNoScroll);

    return () => {
      window.removeEventListener('resize', forceNoScroll);
    };
  }, []);

  return null;
}

export default ForceScrollFix;