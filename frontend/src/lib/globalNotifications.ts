export type GlobalToastSeverity = 'success' | 'error' | 'warning' | 'info';
export type GlobalToastVerticalAnchor = 'top' | 'bottom';
export type GlobalToastHorizontalAnchor = 'left' | 'center' | 'right';

export interface GlobalToastEventDetail {
  message: string;
  severity?: GlobalToastSeverity;
  autoHideDurationMs?: number;
  anchorOrigin?: {
    vertical: GlobalToastVerticalAnchor;
    horizontal: GlobalToastHorizontalAnchor;
  };
}

export const GLOBAL_TOAST_EVENT = 'agr-global-toast';

export const emitGlobalToast = (detail: GlobalToastEventDetail): void => {
  window.dispatchEvent(new CustomEvent<GlobalToastEventDetail>(GLOBAL_TOAST_EVENT, { detail }));
};
