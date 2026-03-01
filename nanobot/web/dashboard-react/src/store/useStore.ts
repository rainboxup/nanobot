import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark' | 'system';
type SystemStatus = 'ready' | 'warning' | 'error';
const MAX_TOASTS = 5;
const DEFAULT_TOAST_DURATION_MS = 4000;
const MIN_TOAST_DURATION_MS = 1000;
const DUPLICATE_WINDOW_MS = 1200;

interface User {
  username: string;
  role: 'owner' | 'admin' | 'member';
  tenant_id?: string;
  is_beta_admin?: boolean;
}

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastAction {
  label: string;
  onClick: () => void | Promise<void>;
}

export interface Toast {
  id: string;
  type: ToastType;
  message: string;
  durationMs: number;
  persist: boolean;
  dedupeKey: string;
  createdAt: number;
  updatedAt: number;
  action?: ToastAction;
}

export interface ToastInput {
  id?: string;
  type: ToastType;
  message: string;
  durationMs?: number;
  persist?: boolean;
  dedupeKey?: string;
  action?: ToastAction;
}

const normalizeToastDuration = (durationMs?: number) => {
  if (durationMs === undefined || durationMs === null) {
    return DEFAULT_TOAST_DURATION_MS;
  }
  const parsed = Number(durationMs);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_TOAST_DURATION_MS;
  }
  return Math.max(MIN_TOAST_DURATION_MS, Math.round(parsed));
};

const normalizeDedupeKey = (toast: Pick<ToastInput, 'type' | 'message' | 'dedupeKey'>) => {
  const customKey = String(toast.dedupeKey || '').trim();
  if (customKey) {
    return `custom:${customKey}`;
  }
  return `auto:${toast.type}:${toast.message}`;
};

const createToastId = () => {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) {
    return uuid;
  }
  return Math.random().toString(36).slice(2, 9);
};

interface AppState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  
  user: User | null;
  setUser: (user: User | null) => void;
  authReady: boolean;
  setAuthReady: (ready: boolean) => void;
  
  systemStatus: SystemStatus;
  setSystemStatus: (status: SystemStatus) => void;
  
  toasts: Toast[];
  addToast: (toast: ToastInput) => void;
  removeToast: (id: string) => void;

  lastSettingsPath: string;
  setLastSettingsPath: (path: string) => void;
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      theme: 'system',
      setTheme: (theme) => set({ theme }),
      
      user: null,
      setUser: (user) =>
        set({
          user: user
            ? {
                username: String(user.username || ""),
                role: String(user.role || "member").toLowerCase() as any,
                tenant_id: String(user.tenant_id || ""),
                is_beta_admin: Boolean((user as any).is_beta_admin),
              }
            : null,
        }),

      authReady: false,
      setAuthReady: (ready) => set({ authReady: ready }),
      
      systemStatus: 'ready',
      setSystemStatus: (status) => set({ systemStatus: status }),
      
      toasts: [],
      addToast: (toastInput) =>
        set((state) => {
          const message = String(toastInput.message || '').trim();
          if (!message) {
            return { toasts: state.toasts };
          }

          const actionLabel = String(toastInput.action?.label || '').trim();
          const nextAction =
            actionLabel && typeof toastInput.action?.onClick === 'function'
              ? { label: actionLabel, onClick: toastInput.action.onClick }
              : undefined;

          const now = Date.now();
          const explicitId = String(toastInput.id || '').trim();
          const dedupeKey = normalizeDedupeKey({
            type: toastInput.type,
            message,
            dedupeKey: toastInput.dedupeKey,
          });
          const duplicateToast = explicitId
            ? undefined
            : state.toasts.find(
                (t) => t.dedupeKey === dedupeKey && now - t.updatedAt <= DUPLICATE_WINDOW_MS
              );
          const nextId = explicitId || duplicateToast?.id || createToastId();
          const existingToast = state.toasts.find((t) => t.id === nextId) ?? duplicateToast;
          const nextToast: Toast = {
            id: nextId,
            type: toastInput.type,
            message,
            durationMs: normalizeToastDuration(toastInput.durationMs),
            persist: Boolean(toastInput.persist),
            dedupeKey,
            action: nextAction,
            createdAt: existingToast?.createdAt ?? now,
            updatedAt: now,
          };
          const nextToasts = state.toasts.filter((t) => t.id !== nextId);
          nextToasts.push(nextToast);
          if (nextToasts.length > MAX_TOASTS) {
            nextToasts.splice(0, nextToasts.length - MAX_TOASTS);
          }
          return { toasts: nextToasts };
        }),
      removeToast: (id) =>
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        })),

      lastSettingsPath: 'providers',
      setLastSettingsPath: (path) => set({ lastSettingsPath: path }),
    }),
    {
      name: 'nanobot-storage',
      partialize: (state) => ({ theme: state.theme, lastSettingsPath: state.lastSettingsPath }),
    }
  )
);
