export type GlobalToastSeverity = 'success' | 'error' | 'warning' | 'info';

export interface GlobalToastEventDetail {
  message: string;
  severity?: GlobalToastSeverity;
}

export const GLOBAL_TOAST_EVENT = 'agr-global-toast';

export const emitGlobalToast = (detail: GlobalToastEventDetail): void => {
  window.dispatchEvent(new CustomEvent<GlobalToastEventDetail>(GLOBAL_TOAST_EVENT, { detail }));
};
