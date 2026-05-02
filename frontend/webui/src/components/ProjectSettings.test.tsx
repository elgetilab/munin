import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { ProjectSettings } from './ProjectSettings';
import type { Project, Persona } from '../lib/types';

const mockProject: Project = {
  id: 'p1',
  user_email: 'test@test.com',
  name: 'ML Research',
  description: 'Machine learning stuff',
  instructions: 'Focus on transformers',
  default_persona: null,
  archived: false,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  conversation_count: 2,
};

const mockPersonas: Persona[] = [
  { id: 'chat', name: 'Meitner - Chat', description: 'General', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] },
  { id: 'code', name: 'Turing - Code', description: 'Code', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] },
];

function renderSettings(overrides: Partial<Parameters<typeof ProjectSettings>[0]> = {}) {
  const props = {
    project: mockProject,
    personas: mockPersonas,
    onClose: vi.fn(),
    onUpdated: vi.fn(),
    ...overrides,
  };
  return { ...render(<ProjectSettings {...props} />), props };
}

describe('ProjectSettings', () => {
  it('renders form with project data loaded from API', async () => {
    // The MSW handler for GET /api/projects/p1 returns the mock project
    renderSettings();

    await waitFor(() => {
      expect(screen.getByDisplayValue('ML Research')).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('Machine learning stuff')).toBeInTheDocument();
    expect(screen.getByText('Project Settings')).toBeInTheDocument();
  });

  it('empty name shows error on save attempt', async () => {
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByDisplayValue('ML Research')).toBeInTheDocument();
    });

    const nameInput = screen.getByDisplayValue('ML Research');
    await user.clear(nameInput);

    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(screen.getByText('Name is required')).toBeInTheDocument();
  });

  it('save calls updateProject with correct payload and fires onUpdated', async () => {
    const user = userEvent.setup();

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/projects/p1', async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ ...mockProject, ...capturedBody });
      }),
    );

    const { props } = renderSettings();

    await waitFor(() => {
      expect(screen.getByDisplayValue('ML Research')).toBeInTheDocument();
    });

    const nameInput = screen.getByDisplayValue('ML Research');
    await user.clear(nameInput);
    await user.type(nameInput, 'Updated Name');

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody!.name).toBe('Updated Name');
    expect(props.onUpdated).toHaveBeenCalled();
  });

  it('archived checkbox toggles in payload', async () => {
    const user = userEvent.setup();

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/projects/p1', async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ ...mockProject, ...capturedBody });
      }),
    );

    renderSettings();

    await waitFor(() => {
      expect(screen.getByDisplayValue('ML Research')).toBeInTheDocument();
    });

    const archivedCheckbox = screen.getByRole('checkbox', { name: /archived/i });
    expect(archivedCheckbox).not.toBeChecked();

    await user.click(archivedCheckbox);
    expect(archivedCheckbox).toBeChecked();

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody!.archived).toBe(true);
  });
});
