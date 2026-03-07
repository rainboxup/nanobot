import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Skills } from './Skills';
import { api } from '@/src/lib/api';
import { useStore } from '@/src/store/useStore';

vi.mock('react-syntax-highlighter', () => ({
  Prism: ({ children }: { children: unknown }) => <pre data-testid="syntax-highlighter">{children as any}</pre>,
}));

vi.mock('react-syntax-highlighter/dist/esm/styles/prism', () => ({
  vscDarkPlus: {},
}));

vi.mock('@/src/components/ui/drawer', () => ({
  Drawer: ({ isOpen, title, description, children, footer }: any) => {
    if (!isOpen) return null;
    return (
      <div role="dialog" aria-label={title || 'drawer'}>
        {title ? <h2>{title}</h2> : null}
        {description ? <p>{description}</p> : null}
        <div>{children}</div>
        <div>{footer}</div>
      </div>
    );
  },
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
      delete: vi.fn(),
      patch: vi.fn(),
      put: vi.fn(),
      fetch: vi.fn(),
    },
    ApiError: MockApiError,
  };
});

vi.mock('@/src/store/useStore', () => ({
  useStore: vi.fn(),
}));

type Role = 'member' | 'admin' | 'owner';

const mockedApiGet = vi.mocked(api.get);
const mockedUseStore = vi.mocked(useStore);

const managedInstalledSkill = {
  name: 'managed-installed-skill',
  source: 'managed',
  origin_source: 'store',
  description: 'Installed managed skill',
  path: 'managed://managed-installed-skill',
};

const managedCatalogSkill = {
  name: 'managed-catalog-skill',
  source: 'managed',
  origin_source: 'store',
  install_source: 'local',
  installed: false,
  category: '已安装',
  description: 'Catalog managed skill',
  author: 'Nanobot',
  version: '1.0.0',
};

const managedSkillDetail = {
  name: 'managed-catalog-skill',
  source: 'managed',
  origin_source: 'store',
  install_source: 'local',
  description: 'Catalog managed skill',
  path: 'managed://managed-catalog-skill',
  content: '# Skill content',
  metadata: {},
};

function mockStore(role: Role) {
  mockedUseStore.mockReturnValue({
    addToast: vi.fn(),
    user: { username: `${role}-user`, role },
  } as any);
}

function mockApi() {
  mockedApiGet.mockImplementation(async (path: string) => {
    if (path === '/api/skills') {
      return [managedInstalledSkill] as any;
    }
    if (path.startsWith('/api/skills/catalog/v2?')) {
      if (path.includes('source=local')) {
        return { items: [managedCatalogSkill], next_cursor: null, partial: false, warnings: [] } as any;
      }
      if (path.includes('source=clawhub')) {
        return { items: [], next_cursor: null, partial: false, warnings: [] } as any;
      }
    }
    if (path === '/api/mcp/catalog' || path === '/api/mcp/servers') {
      return [] as any;
    }
    if (path === '/api/skills/managed-catalog-skill' || path === '/api/skills/managed-installed-skill') {
      return managedSkillDetail as any;
    }
    throw new Error(`Unhandled api.get mock for: ${path}`);
  });
}

describe('Skills', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi();
  });

  it('shows productized source labels and hides technical source fields for members', async () => {
    mockStore('member');
    render(<Skills />);

    expect(await screen.findAllByText('平台托管')).not.toHaveLength(0);
    expect(screen.queryByText(/^managed$/i)).not.toBeInTheDocument();
    expect(screen.queryByText('技术详情请查看技能详情面板')).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: '详情' }));

    await screen.findByRole('dialog');
    expect(await screen.findByText('来源: 平台托管')).toBeInTheDocument();
    expect(screen.queryByText('技术详情')).not.toBeInTheDocument();
    expect(screen.queryByText(/原始来源：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/安装源：/)).not.toBeInTheDocument();
  });

  it('shows technical source fields for owners in the detail drawer', async () => {
    mockStore('owner');
    render(<Skills />);

    const detailButtons = await screen.findAllByRole('button', { name: '详情' });
    fireEvent.click(detailButtons[0]);

    expect(await screen.findByText('技术详情')).toBeInTheDocument();
    expect(screen.getByText('来源：managed')).toBeInTheDocument();
    expect(screen.getByText('原始来源：store')).toBeInTheDocument();
    expect(screen.getByText('安装源：local')).toBeInTheDocument();
  });

  it('does not allow members to search by hidden technical source tokens', async () => {
    mockStore('member');
    render(<Skills />);

    await screen.findByText('managed-catalog-skill');
    fireEvent.change(screen.getByPlaceholderText('搜索技能...'), { target: { value: 'store' } });

    await waitFor(() => {
      expect(screen.queryByText('managed-catalog-skill')).not.toBeInTheDocument();
    });
    expect(screen.queryByText('技术详情请查看技能详情面板')).not.toBeInTheDocument();
  });

  it('shows technical source fields for admins in the detail drawer', async () => {
    mockStore('admin');
    render(<Skills />);

    const detailButtons = await screen.findAllByRole('button', { name: '详情' });
    fireEvent.click(detailButtons[0]);

    expect(await screen.findByText('技术详情')).toBeInTheDocument();
    expect(screen.getByText('来源：managed')).toBeInTheDocument();
    expect(screen.getByText('原始来源：store')).toBeInTheDocument();
    expect(screen.getByText('安装源：local')).toBeInTheDocument();
  });

  it('renders productized labels for installed skills instead of raw managed token', async () => {
    mockStore('member');
    render(<Skills />);

    const installedTitle = await screen.findByText('managed-installed-skill');
    const installedCard = installedTitle.closest('[class*="cursor-pointer"]') ?? installedTitle.parentElement;
    expect(installedCard).not.toBeNull();

    await waitFor(() => {
      expect(within(installedCard as HTMLElement).getByText('平台托管')).toBeInTheDocument();
    });
    expect(within(installedCard as HTMLElement).queryByText(/^managed$/i)).not.toBeInTheDocument();
  });
});
