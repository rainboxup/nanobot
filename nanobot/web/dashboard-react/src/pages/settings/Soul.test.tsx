import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Soul } from './Soul';
import { api } from '@/src/lib/api';
import { useStore } from '@/src/store/useStore';

vi.mock('@/src/pages/HelpDoc', () => ({
  helpDocHref: () => '/help/effective-policy-and-soul',
}));

vi.mock('@/src/lib/api', () => {
  class MockApiError extends Error {
    status: number;
    detail: string;

    constructor(status: number, detail: string) {
      super(detail || `HTTP ${status}`);
      this.status = status;
      this.detail = detail || `HTTP ${status}`;
    }
  }

  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
      fetch: vi.fn(),
    },
    ApiError: MockApiError,
    formatTime: (value: string | null | undefined) => String(value || '-'),
  };
});

vi.mock('@/src/store/useStore', () => ({
  useStore: vi.fn(),
}));

const mockedApiGet = vi.mocked(api.get);
const mockedUseStore = vi.mocked(useStore);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mockOwnerStore() {
  mockedUseStore.mockReturnValue({
    addToast: vi.fn(),
    user: { username: 'owner-user', role: 'owner' },
  } as any);
}

describe('Soul', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockOwnerStore();
  });

  it('shows extended baseline summary and lets owners query a tenant effective baseline', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/api/soul') {
        return {
          runtime_mode: 'multi',
          runtime_scope: 'tenant',
          writable: true,
          takes_effect: 'next_message',
          subject: { tenant_id: 'tenant-alpha' },
          workspace: { filename: 'SOUL.md', exists: true, content: '# Tenant soul' },
          effective: { merged_content: '# effective', layers: [] },
          baseline: {
            selected_version_id: 'base-v2',
            effective_version_id: 'base-v2',
            candidate_version_id: 'base-v3',
            control_version_id: 'base-v1',
            strategy: 'canary',
            canary_percent: 25,
            bucket: 17,
            is_canary: true,
          },
        } as any;
      }
      if (path === '/api/admin/baseline/versions') {
        return {
          versions: [
            { version_id: 'base-v3', label: 'candidate' },
            { version_id: 'base-v2', label: 'stable' },
            { version_id: 'base-v1', label: 'control' },
          ],
        } as any;
      }
      if (path === '/api/admin/baseline/effective?tenant_id=tenant-beta') {
        return {
          selected_version_id: 'base-v3',
          effective_version_id: 'base-v3',
          candidate_version_id: 'base-v3',
          control_version_id: 'base-v1',
          strategy: 'canary',
          canary_percent: 25,
          bucket: 17,
          is_canary: true,
          rollout: {
            strategy: 'canary',
            candidate_version_id: 'base-v3',
            control_version_id: 'base-v1',
          },
        } as any;
      }
      throw new Error(`Unhandled api.get mock for: ${path}`);
    });

    render(<Soul />);

    expect(await screen.findByText(/candidate:\s*base-v3/i)).toBeInTheDocument();
    expect(screen.getByText(/control:\s*base-v1/i)).toBeInTheDocument();
    expect(screen.getByText(/bucket:\s*17/i)).toBeInTheDocument();
    expect(await screen.findByDisplayValue('tenant-alpha')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('输入 tenant_id 查看 effective baseline'), {
      target: { value: 'tenant-beta' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查看 effective baseline' }));

    expect(await screen.findByText(/effective:\s*base-v3/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/api/admin/baseline/effective?tenant_id=tenant-beta');
    });
  });

  it('keeps rollback default aligned with the effective baseline when versions load first', async () => {
    const soulRequest = deferred<any>();
    const versionsResponse = {
      versions: [
        { version_id: 'base-v3', label: 'candidate' },
        { version_id: 'base-v2', label: 'stable' },
        { version_id: 'base-v1', label: 'control' },
      ],
    };
    let versionsCallCount = 0;

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/api/soul') {
        return soulRequest.promise;
      }
      if (path === '/api/admin/baseline/versions') {
        versionsCallCount += 1;
        if (versionsCallCount === 1) return Promise.resolve(versionsResponse as any);
        return Promise.resolve(versionsResponse as any);
      }
      throw new Error(`Unhandled api.get mock for: ${path}`);
    });

    render(<Soul />);

    soulRequest.resolve({
      runtime_mode: 'multi',
      runtime_scope: 'tenant',
      writable: true,
      takes_effect: 'next_message',
      subject: { tenant_id: 'tenant-race' },
      workspace: { filename: 'SOUL.md', exists: true, content: '# Tenant soul' },
      effective: { merged_content: '# effective', layers: [] },
      baseline: {
        selected_version_id: 'base-v3',
        effective_version_id: 'base-v2',
        candidate_version_id: 'base-v3',
        control_version_id: 'base-v1',
        strategy: 'canary',
        canary_percent: 10,
        bucket: 11,
        is_canary: false,
      },
    } as any);

    await screen.findByText(/candidate:\s*base-v3/i);

    await waitFor(() => {
      expect(screen.getByDisplayValue('base-v2')).toBeInTheDocument();
    });
  });

  it('disables mutable controls after the initial load fails', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/api/soul') {
        throw new Error('Soul load failed');
      }
      if (path === '/api/admin/baseline/versions') {
        return {
          versions: [{ version_id: 'base-v1', label: 'stable' }],
        } as any;
      }
      throw new Error(`Unhandled api.get mock for: ${path}`);
    });

    render(<Soul />);

    expect(await screen.findByText('Soul load failed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();
    expect(screen.getByPlaceholderText('在这里编辑 SOUL.md 内容...')).toBeDisabled();
    expect(screen.getByPlaceholderText('输入 overlay 内容后点击预览...')).toBeDisabled();
    expect(screen.getByRole('button', { name: '预览' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });
});
