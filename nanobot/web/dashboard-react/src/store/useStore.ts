import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark' | 'system';
type SystemStatus = 'ready' | 'warning' | 'error';

interface User {
  username: string;
  role: 'owner' | 'admin' | 'member';
  tenant_id?: string;
}

interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  durationMs?: number;
  persist?: boolean;
  createdAt: number;
  updatedAt: number;
  action?: {
    label: string;
    onClick: () => void;
  };
}

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
  addToast: (
    toast: Omit<Toast, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ) => void;
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
      setUser: (user) => set({ user }),

      authReady: false,
      setAuthReady: (ready) => set({ authReady: ready }),
      
      systemStatus: 'ready',
      setSystemStatus: (status) => set({ systemStatus: status }),
      
      toasts: [],
      addToast: (toast) =>
        set((state) => {
          const nextId = String(toast.id || Math.random().toString(36).substring(2, 9));
          const now = Date.now();
          const existing = state.toasts.find((t) => t.id === nextId);
          const nextToast: Toast = {
            ...toast,
            id: nextId,
            createdAt: existing?.createdAt ?? now,
            updatedAt: now,
          };
          const next = state.toasts.filter((t) => t.id !== nextId);
          next.push(nextToast);
          // Keep toast list bounded.
          const max = 5;
          return { toasts: next.slice(Math.max(0, next.length - max)) };
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
