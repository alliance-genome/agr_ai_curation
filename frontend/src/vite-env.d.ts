/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEV_MODE: string;
  readonly VITE_DEV_USER_SUB: string;
  readonly VITE_DEV_USER_EMAIL: string;
  readonly VITE_DEV_USER_NAME: string;
  readonly VITE_DEV_USER_GROUPS: string;
  readonly VITE_APP_VERSION: string;
  readonly VITE_GIT_SHA: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
