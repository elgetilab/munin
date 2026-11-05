import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/msw-server';
import { MOCK_ADMIN_GROUPS } from '../../test/msw-handlers';
import { GroupsTab } from './GroupsTab';

// Characterization tests, written before the fetch-on-mount restructure so
// there is something for it to violate. They describe what a user sees, not
// how the component stores it, which is exactly the part that must survive
// moving the loading flag out of load().

const ADMIN = 'https://auth.muninai.org/admin';

describe('GroupsTab', () => {
  it('shows a loading state until the groups arrive', async () => {
    render(<GroupsTab />);
    expect(screen.getByText('Loading groups...')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
    expect(screen.queryByText('Loading groups...')).not.toBeInTheDocument();
  });

  it('renders a row per group with slug and member count', async () => {
    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());

    for (const g of MOCK_ADMIN_GROUPS) {
      expect(screen.getByText(g.display_name)).toBeInTheDocument();
      expect(screen.getByText(g.slug)).toBeInTheDocument();
    }
    // ML Research has 2 members, Bio Lab has 0.
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('surfaces a failed load instead of rendering an empty table', async () => {
    server.use(http.get(`${ADMIN}/groups`, () => new HttpResponse(null, { status: 500 })));
    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('renders the empty state when there are no groups', async () => {
    server.use(http.get(`${ADMIN}/groups`, () => HttpResponse.json({ groups: [] })));
    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByText('No groups yet.')).toBeInTheDocument());
  });

  it('re-reads the list after a group is created, so the new row appears', async () => {
    const user = userEvent.setup();
    let created = false;
    server.use(
      http.get(`${ADMIN}/groups`, () => HttpResponse.json({
        groups: created
          ? [...MOCK_ADMIN_GROUPS, { slug: 'new-lab', display_name: 'New Lab', member_count: 0,
              created_at: new Date().toISOString(), updated_at: new Date().toISOString() }]
          : MOCK_ADMIN_GROUPS,
      })),
      http.post(`${ADMIN}/groups`, () => { created = true; return HttpResponse.json({ group: {} }); }),
    );

    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
    expect(screen.queryByText('New Lab')).not.toBeInTheDocument();

    await user.click(screen.getByText('+ Add group'));
    await user.type(screen.getByPlaceholderText('lowercase, no spaces'), 'new-lab');
    await user.type(screen.getByPlaceholderText('e.g. Elgeti Lab (Leipzig)'), 'New Lab');
    await user.click(screen.getByText('Create'));

    // The refresh is the behaviour under test: the row must appear without a
    // remount, and the rows that were already there must stay.
    await waitFor(() => expect(screen.getByText('New Lab')).toBeInTheDocument());
    expect(screen.getByText('ML Research')).toBeInTheDocument();
  });

  // With enough members the dialog is taller than the screen. Save and Delete
  // live in the footer, outside the part that scrolls, so they stay reachable.
  it('keeps the edit dialog actions outside its scrolling body', async () => {
    const user = userEvent.setup();
    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]);

    const dialog = screen.getByRole('dialog', { name: 'Edit ML Research' });
    const body = within(dialog).getByText('Slug (read-only)').closest('.overflow-y-auto');
    expect(body).not.toBeNull();
    for (const name of ['Save', 'Close', 'Delete group']) {
      expect(body).not.toContainElement(within(dialog).getByRole('button', { name }));
    }
  });

  it('retries a failed first load', async () => {
    const user = userEvent.setup();
    let fail = true;
    server.use(http.get(`${ADMIN}/groups`, () => (
      fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json({ groups: MOCK_ADMIN_GROUPS })
    )));

    render(<GroupsTab />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps the table and the open dialog when a refresh fails', async () => {
    const user = userEvent.setup();
    let fail = false;
    server.use(
      http.get(`${ADMIN}/groups`, () => (
        fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json({ groups: MOCK_ADMIN_GROUPS })
      )),
      http.delete(`${ADMIN}/groups/:slug/members/:userId`, () => {
        fail = true;
        return HttpResponse.json({ members: [] });
      }),
    );
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    try {
      render(<GroupsTab />);
      await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
      await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
      await user.click(await screen.findByRole('button', { name: 'Remove' }));

      await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not refresh/i));
      expect(screen.getByRole('table')).toBeInTheDocument();
      expect(screen.getByRole('dialog', { name: 'Edit ML Research' })).toBeInTheDocument();
    } finally {
      confirm.mockRestore();
    }
  });

  describe('add group dialog', () => {
    async function open() {
      const user = userEvent.setup();
      render(<GroupsTab />);
      await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
      await user.click(screen.getByText('+ Add group'));
      return user;
    }

    it('ignores a backdrop click once something has been typed, and Escape still closes', async () => {
      const user = await open();
      await user.type(screen.getByPlaceholderText('lowercase, no spaces'), 'lab');

      await user.click(screen.getByTestId('dialog-backdrop'));
      expect(screen.getByRole('dialog', { name: 'Add group' })).toBeInTheDocument();

      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('submits through the form once valid, and says what is missing until then', async () => {
      let created = 0;
      server.use(http.post(`${ADMIN}/groups`, () => { created += 1; return HttpResponse.json({ group: {} }); }));
      const user = await open();
      const form = screen.getByPlaceholderText('lowercase, no spaces').closest('form')!;

      expect(screen.getByText(/enter a valid slug and a display name/i)).toBeInTheDocument();
      fireEvent.submit(form);
      expect(created).toBe(0);

      await user.type(screen.getByPlaceholderText('lowercase, no spaces'), 'lab');
      await user.type(screen.getByPlaceholderText('e.g. Elgeti Lab (Leipzig)'), 'Lab');
      expect(screen.queryByText(/to create this group/i)).not.toBeInTheDocument();

      fireEvent.submit(form);
      await waitFor(() => expect(created).toBe(1));
    });
  });

  describe('edit group dialog', () => {
    const person = (id: number, name: string, email: string) => ({
      id, first_name: name, last_name: '', name, role: 'user', group: null, username: null,
      primary_email: email, emails: [email], created_at: '', updated_at: '',
    });

    async function openEdit(users: ReturnType<typeof person>[]) {
      const user = userEvent.setup();
      server.use(
        http.get(`${ADMIN}/users`, () => HttpResponse.json({ users })),
        http.get(`${ADMIN}/groups/:slug/members`, () => HttpResponse.json({ members: [] })),
      );
      render(<GroupsTab />);
      await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
      await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
      await screen.findByText('No members yet.');
      return user;
    }

    it('shows a name and a long email on separate lines', async () => {
      const email = 'a.very.long.address.that.would.squeeze.the.name@test.com';
      const user = await openEdit([person(10, 'Rosalind', email)]);

      await user.type(screen.getByPlaceholderText('search users by name or email...'), 'rosa');

      const row = screen.getByRole('button', { name: new RegExp(email) });
      expect(within(row).getByText('Rosalind')).toHaveClass('block');
      expect(within(row).getByText(email)).toHaveClass('block');
    });

    it('says when the results are capped', async () => {
      const many = Array.from({ length: 11 }, (_, i) => person(10 + i, `Sam ${i}`, `sam${i}@test.com`));
      const user = await openEdit(many);

      await user.type(screen.getByPlaceholderText('search users by name or email...'), 'sam');

      expect(screen.getByText('Showing 8 of 11, refine your search.')).toBeInTheDocument();
    });

    it('stays quiet when every result fits', async () => {
      const user = await openEdit([person(10, 'Sam', 'sam@test.com')]);
      await user.type(screen.getByPlaceholderText('search users by name or email...'), 'sam');
      expect(screen.queryByText(/refine your search/)).not.toBeInTheDocument();
    });

    it('uses the same role names as the Users tab', async () => {
      const user = userEvent.setup();
      render(<GroupsTab />);
      await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
      await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]);

      const select = await screen.findByRole('combobox');
      expect(within(select).getByRole('option', { name: 'User' })).toBeInTheDocument();
      expect(within(select).getByRole('option', { name: 'Group leader' })).toBeInTheDocument();
    });

    it('ignores a backdrop click only while the name has unsaved changes', async () => {
      const user = await openEdit([]);
      await user.type(screen.getByDisplayValue('ML Research'), ' 2');

      await user.click(screen.getByTestId('dialog-backdrop'));
      expect(screen.getByRole('dialog')).toBeInTheDocument();

      await user.clear(screen.getByDisplayValue('ML Research 2'));
      await user.type(screen.getByRole('dialog').querySelector('form input')!, 'ML Research');
      await user.click(screen.getByTestId('dialog-backdrop'));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});
