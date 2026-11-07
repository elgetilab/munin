import { render, screen, waitFor, within } from '@testing-library/react';
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

  describe('clearing fields', () => {
    it.each([
      ['description', 'Machine learning stuff'],
      ['instructions', 'Focus on transformers'],
    ])('sends null for an emptied %s', async (field, value) => {
      const user = userEvent.setup();
      let body: Record<string, unknown> = {};
      server.use(
        http.get('/api/projects/p1', () => HttpResponse.json(mockProject)),
        http.patch('/api/projects/p1', async ({ request }) => {
          body = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ ...mockProject, ...body });
        }),
      );
      const { props } = renderSettings();
      await user.clear(screen.getByDisplayValue(value));
      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(props.onUpdated).toHaveBeenCalled());
      expect(body).toHaveProperty(field, null);
    });

    it('sends null when the persona goes back to the global default', async () => {
      const user = userEvent.setup();
      const project = { ...mockProject, default_persona: 'code' };
      let body: Record<string, unknown> = {};
      server.use(
        http.get('/api/projects/p1', () => HttpResponse.json(project)),
        http.patch('/api/projects/p1', async ({ request }) => {
          body = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ ...project, ...body });
        }),
      );
      const { props } = renderSettings({ project });
      await user.selectOptions(screen.getByDisplayValue('Turing - Code'), 'Use my default from Settings');
      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(props.onUpdated).toHaveBeenCalled());
      expect(body).toHaveProperty('default_persona', null);
    });
  });

  describe('limits', () => {
    it('matches the limits the server enforces', async () => {
      server.use(http.get('/api/projects/p1', () => HttpResponse.json(mockProject)));
      renderSettings();
      expect(screen.getByDisplayValue('ML Research')).toHaveAttribute('maxlength', '200');
      expect(screen.getByDisplayValue('Machine learning stuff')).toHaveAttribute('maxlength', '1000');
      expect(screen.getByDisplayValue('Focus on transformers')).toHaveAttribute('maxlength', '2000');
      expect(screen.getByText(`${mockProject.instructions.length}/2000`)).toBeInTheDocument();
    });

    it("shows the server's reason when a save is rejected", async () => {
      const user = userEvent.setup();
      server.use(http.patch('/api/projects/p1', () => HttpResponse.json(
        { detail: { error: { message: 'instructions exceed 2000-character cap (got 2001)' } } },
        { status: 400 },
      )));
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Save' }));
      expect(await screen.findByText('instructions exceed 2000-character cap (got 2001)')).toBeInTheDocument();
    });
  });

  describe('loading', () => {
    const projectB: Project = { ...mockProject, id: 'p9', name: 'Other', description: 'Other notes', instructions: 'Other rules' };
    const props = { personas: mockPersonas, onClose: vi.fn(), onUpdated: vi.fn() };

    it("shows the new project's values at once when another project is opened", async () => {
      server.use(
        http.get('/api/projects/p1', () => HttpResponse.json(mockProject)),
        // Never answers, so what is on screen can only have come from the prop.
        http.get('/api/projects/p9', () => new Promise<Response>(() => {})),
      );
      const { rerender } = render(<ProjectSettings {...props} project={mockProject} />);
      rerender(<ProjectSettings {...props} project={projectB} />);
      expect(screen.getByDisplayValue('Other')).toBeInTheDocument();
      expect(screen.getByDisplayValue('Other notes')).toBeInTheDocument();
      expect(screen.queryByDisplayValue('ML Research')).not.toBeInTheDocument();
    });

    it('drops a slow response for the project that was left', async () => {
      let finishA!: () => void;
      const heldA = new Promise<void>(resolve => { finishA = resolve; });
      let answeredA = false;
      server.use(
        http.get('/api/projects/p1', async () => {
          await heldA;
          answeredA = true;
          return HttpResponse.json(mockProject);
        }),
        http.get('/api/projects/p9', () => HttpResponse.json({ ...projectB, name: 'Other, fresh' })),
      );
      const { rerender } = render(<ProjectSettings {...props} project={mockProject} />);
      rerender(<ProjectSettings {...props} project={projectB} />);
      await screen.findByDisplayValue('Other, fresh');
      finishA();
      await waitFor(() => expect(answeredA).toBe(true));
      await new Promise(resolve => setTimeout(resolve, 20));
      expect(screen.getByDisplayValue('Other, fresh')).toBeInTheDocument();
      expect(screen.queryByDisplayValue('ML Research')).not.toBeInTheDocument();
    });

    it('a late response does not overwrite what the user typed', async () => {
      const user = userEvent.setup();
      let finish!: () => void;
      const held = new Promise<void>(resolve => { finish = resolve; });
      let answered = false;
      server.use(http.get('/api/projects/p1', async () => {
        await held;
        answered = true;
        return HttpResponse.json({ ...mockProject, description: 'From the server' });
      }));
      renderSettings();
      const description = screen.getByDisplayValue('Machine learning stuff');
      await user.clear(description);
      await user.type(description, 'Typed');
      finish();
      await waitFor(() => expect(answered).toBe(true));
      await new Promise(resolve => setTimeout(resolve, 20));
      expect(description).toHaveValue('Typed');
    });

    it('shows a failed load and keeps Save off until Retry works', async () => {
      const user = userEvent.setup();
      let fail = true;
      server.use(http.get('/api/projects/p1', () => (fail
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ ...mockProject, description: 'Fresh' }))));
      renderSettings();
      expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load this project/);
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
      fail = false;
      await user.click(screen.getByRole('button', { name: 'Retry' }));
      await waitFor(() => expect(screen.getByDisplayValue('Fresh')).toBeInTheDocument());
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    });
  });

  describe('accessible names', () => {
    it('names the back button and ties each label to its control', () => {
      renderSettings();
      expect(screen.getByRole('button', { name: 'Back to chat' })).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('ML Research');
      expect(screen.getByLabelText('Description')).toHaveValue('Machine learning stuff');
      expect(screen.getByLabelText('Instructions')).toHaveValue('Focus on transformers');
      expect(screen.getByLabelText('Default persona').tagName).toBe('SELECT');
    });
  });

  describe('wording and controls', () => {
    it('closes from a visible Back to chat button in the header', async () => {
      const user = userEvent.setup();
      const { props } = renderSettings();
      const back = screen.getByRole('button', { name: 'Back to chat' });
      expect(back).toHaveTextContent('Back to chat');
      await user.click(back);
      expect(props.onClose).toHaveBeenCalled();
    });

    it('describes instructions and personas in plain words', () => {
      renderSettings();
      expect(screen.getByLabelText('Instructions')).toHaveAccessibleDescription('Munin reads these at the start of every chat in this project.');
      expect(screen.queryByText(/system prompt/)).not.toBeInTheDocument();
      const persona = screen.getByLabelText('Default persona');
      expect(persona).toHaveAccessibleDescription(/A persona is a preset way for Munin to work/);
      expect(within(persona).getByRole('option', { name: 'Use my default from Settings' })).toHaveValue('');
    });

    it('says Saved, like the other settings pages, after a save', async () => {
      const user = userEvent.setup();
      renderSettings();
      await waitFor(() => expect(screen.getByDisplayValue('ML Research')).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: 'Save' }));
      expect(await screen.findByRole('button', { name: 'Saved' })).toBeInTheDocument();
    });
  });
});
