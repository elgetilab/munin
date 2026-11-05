import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/msw-server';
import { UsersTab } from './UsersTab';

// Characterization tests written before the fetch-on-mount restructure. The
// point is the observable contract: what renders, what the filter does, and
// that a failed load says so rather than showing an empty table.

const ADMIN = 'https://auth.muninai.org/admin';

describe('UsersTab', () => {
  it('shows a loading state until users and groups arrive', async () => {
    render(<UsersTab />);
    expect(screen.getByText('Loading users...')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
  });

  it('renders a row per user', async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
    expect(screen.getByText('Grace')).toBeInTheDocument();
    expect(screen.getByText('Alan')).toBeInTheDocument();
    expect(screen.getByText('ada@test.com')).toBeInTheDocument();
  });

  it('filters by name and reports the filtered count', async () => {
    const user = userEvent.setup();
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
    expect(screen.getByText('3 / 3')).toBeInTheDocument();

    await user.type(
      screen.getByRole('textbox', { name: /filter users/i }),
      'grace',
    );

    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    expect(screen.getByText('Grace')).toBeInTheDocument();
    expect(screen.queryByText('Ada')).not.toBeInTheDocument();
  });

  // Both requests are needed to render the table, so either one failing has
  // to surface. This is the case the restructure could most easily break,
  // since it moves the Promise.all into the effect.
  it.each([['users'], ['groups']])('surfaces a failed %s load', async (which) => {
    server.use(http.get(`${ADMIN}/${which}`, () => new HttpResponse(null, { status: 500 })));
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('re-reads the list after a user is created', async () => {
    const user = userEvent.setup();
    let created = false;
    server.use(
      http.get(`${ADMIN}/users`, () => HttpResponse.json({
        users: created
          ? [{ id: 4, first_name: 'Barbara', last_name: 'Liskov', name: 'Barbara Liskov',
               role: 'user', group: null, username: 'barbara', primary_email: 'barbara@test.com',
               emails: ['barbara@test.com'], created_at: '', updated_at: '' }]
          : [],
      })),
      http.post(`${ADMIN}/users`, () => { created = true; return HttpResponse.json({ user: {} }); }),
    );

    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('0 / 0')).toBeInTheDocument());

    await user.click(screen.getByText('+ Add user'));
    await user.type(screen.getByPlaceholderText('Jane'), 'Barbara');
    await user.type(screen.getByPlaceholderText('Doe (optional)'), 'Liskov');
    await user.type(screen.getByPlaceholderText('jane.doe@example.org'), 'barbara@test.com');
    await user.click(screen.getByText('Create'));

    await waitFor(() => expect(screen.getByText('Barbara')).toBeInTheDocument());
  });

  // A phone is too narrow for the table, so it has to pan rather than clip, or
  // the Edit column is out of reach. jsdom has no layout, so the classes are
  // the only thing that can be pinned here.
  it('lets the table scroll sideways with Edit pinned', async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());

    expect(screen.getByRole('table').parentElement).toHaveClass('overflow-x-auto');
    expect(screen.getAllByRole('button', { name: 'Edit' })[0].closest('td')).toHaveClass('sticky', 'right-0');
  });

  // Email changes apply at once and Save stays disabled, so Close is the only
  // way out of the dialog. The table must not be left showing the old address.
  it('re-reads the table after an email change, before the dialog is closed', async () => {
    const user = userEvent.setup();
    let primary = 'ada@test.com';
    const row = () => ({
      id: 1, first_name: 'Ada', last_name: 'Lovelace', name: 'Ada Lovelace', role: 'user',
      group: null, username: null, primary_email: primary,
      emails: ['ada@test.com', 'countess@test.com'], created_at: '', updated_at: '',
    });
    server.use(
      http.get(`${ADMIN}/users`, () => HttpResponse.json({ users: [row()] })),
      http.put(`${ADMIN}/users/1/emails/:email/primary`, ({ params }) => {
        primary = String(params.email);
        return HttpResponse.json(row());
      }),
    );

    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
    expect(within(screen.getByRole('table')).getByText('ada@test.com')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    await user.click(screen.getByRole('button', { name: 'Make primary' }));

    await waitFor(() => expect(within(screen.getByRole('table')).getByText('countess@test.com')).toBeInTheDocument());

    // Reopening starts from the fresh row, not the one the dialog first opened with.
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    const primaryBadge = screen.getByText('Primary', { selector: 'span' });
    expect(primaryBadge.parentElement).toHaveTextContent('countess@test.com');
  });

  it('finds users by the display name of their group as well as the slug', async () => {
    const user = userEvent.setup();
    server.use(http.get(`${ADMIN}/users`, () => HttpResponse.json({ users: [
      { id: 1, first_name: 'Ada', last_name: '', name: 'Ada', role: 'user', group: 'bio-lab', username: null,
        primary_email: 'ada@test.com', emails: ['ada@test.com'], created_at: '', updated_at: '' },
      { id: 2, first_name: 'Alan', last_name: '', name: 'Alan', role: 'user', group: null, username: null,
        primary_email: 'alan@test.com', emails: ['alan@test.com'], created_at: '', updated_at: '' },
    ] })));

    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());

    await user.type(screen.getByRole('textbox', { name: /filter users/i }), 'Bio Lab');

    expect(screen.getByText('Ada')).toBeInTheDocument();
    expect(screen.queryByText('Alan')).not.toBeInTheDocument();
  });

  it('labels the single group it shows as the primary one', async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /primary group/i })).toBeInTheDocument();
  });

  describe('load errors', () => {
    it('retries a failed first load', async () => {
      const user = userEvent.setup();
      let fail = true;
      server.use(http.get(`${ADMIN}/users`, () => (
        fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json({ users: [] })
      )));

      render(<UsersTab />);
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

      fail = false;
      await user.click(screen.getByRole('button', { name: 'Retry' }));

      await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('keeps the table when a refresh after a change fails', async () => {
      const user = userEvent.setup();
      let fail = false;
      server.use(
        http.get(`${ADMIN}/users`, () => (
          fail
            ? new HttpResponse(null, { status: 500 })
            : HttpResponse.json({ users: [
                { id: 1, first_name: 'Ada', last_name: '', name: 'Ada', role: 'user', group: null, username: null,
                  primary_email: 'ada@test.com', emails: ['ada@test.com'], created_at: '', updated_at: '' },
              ] })
        )),
        http.post(`${ADMIN}/users`, () => { fail = true; return HttpResponse.json({ user: {} }); }),
      );

      render(<UsersTab />);
      await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());

      await user.click(screen.getByText('+ Add user'));
      await user.type(screen.getByPlaceholderText('Jane'), 'Barbara');
      await user.type(screen.getByPlaceholderText('jane.doe@example.org'), 'b@test.com');
      await user.click(screen.getByRole('button', { name: 'Create' }));

      await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not refresh/i));
      expect(screen.getByText('Ada')).toBeInTheDocument();
    });
  });

  describe('dialogs', () => {
    async function openAddUser() {
      const user = userEvent.setup();
      render(<UsersTab />);
      await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
      await user.click(screen.getByText('+ Add user'));
      return user;
    }

    it('is a labelled dialog that Escape closes', async () => {
      const user = await openAddUser();
      expect(screen.getByRole('dialog', { name: 'Add user' })).toBeInTheDocument();

      await user.keyboard('{Escape}');

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('ignores a backdrop click once something has been typed', async () => {
      const user = await openAddUser();
      await user.type(screen.getByPlaceholderText('Jane'), 'B');

      await user.click(screen.getByTestId('dialog-backdrop'));
      expect(screen.getByRole('dialog')).toBeInTheDocument();

      await user.clear(screen.getByPlaceholderText('Jane'));
      await user.click(screen.getByTestId('dialog-backdrop'));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('says which fields stop Create, and Enter does nothing until they are filled', async () => {
      let created = 0;
      server.use(http.post(`${ADMIN}/users`, () => { created += 1; return HttpResponse.json({ user: {} }); }));
      const user = await openAddUser();
      const form = screen.getByPlaceholderText('Jane').closest('form')!;

      expect(screen.getByText(/enter a first name and an email address/i)).toBeInTheDocument();
      fireEvent.submit(form);
      expect(created).toBe(0);

      await user.type(screen.getByPlaceholderText('Jane'), 'Barbara');
      expect(screen.getByText(/enter an email address/i)).toBeInTheDocument();

      await user.type(screen.getByPlaceholderText('jane.doe@example.org'), 'b@test.com');
      expect(screen.queryByText(/to create this user/i)).not.toBeInTheDocument();

      // user-event's Enter ignores a submit button outside the form, which a
      // browser honours, so the form's own submit stands in for it.
      fireEvent.submit(form);
      await waitFor(() => expect(created).toBe(1));
    });

    it('adds an alias on Enter without saving the other fields', async () => {
      let added = 0;
      let saved = 0;
      server.use(
        http.post(`${ADMIN}/users/1/emails`, () => {
          added += 1;
          return HttpResponse.json({
            id: 1, first_name: 'Ada', last_name: 'Lovelace', name: 'Ada Lovelace', role: 'user', group: null,
            username: null, primary_email: 'ada@test.com', emails: ['ada@test.com', 'a2@test.com'],
            created_at: '', updated_at: '',
          });
        }),
        http.patch(`${ADMIN}/users/1`, () => { saved += 1; return HttpResponse.json({ user: {} }); }),
      );
      const user = userEvent.setup();
      render(<UsersTab />);
      await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
      await user.click(within(screen.getByText('ada@test.com').closest('tr')!).getByRole('button', { name: 'Edit' }));

      await user.type(screen.getByRole('dialog').querySelector('input[placeholder="(optional)"]')!, 'x');
      await user.type(screen.getByPlaceholderText('add an alias...'), 'a2@test.com{Enter}');

      await waitFor(() => expect(added).toBe(1));
      expect(saved).toBe(0);
      expect(within(screen.getByRole('dialog')).getByText('a2@test.com')).toBeInTheDocument();
    });
  });

  describe('removing an email', () => {
    // Asks with confirm declined, so nothing is removed, and returns the question.
    async function promptFor(email: string) {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      try {
        const user = userEvent.setup();
        server.use(http.get(`${ADMIN}/users`, () => HttpResponse.json({ users: [
          { id: 1, first_name: 'Ada', last_name: 'Lovelace', name: 'Ada Lovelace', role: 'user', group: null,
            username: null, primary_email: 'b@test.com', emails: ['b@test.com', 'd@test.com', 'c@test.com'],
            created_at: '', updated_at: '' },
        ] })));
        render(<UsersTab />);
        await waitFor(() => expect(screen.getByText('Ada')).toBeInTheDocument());
        await user.click(screen.getByRole('button', { name: 'Edit' }));

        const dialog = within(screen.getByRole('dialog'));
        await user.click(within(dialog.getByText(email).parentElement!).getByRole('button', { name: 'Remove' }));

        expect(confirm).toHaveBeenCalledTimes(1);
        return confirm.mock.calls[0][0] as string;
      } finally {
        confirm.mockRestore();
      }
    }

    it('warns about sessions when removing a secondary address', async () => {
      const text = await promptFor('c@test.com');
      expect(text).toContain('Remove c@test.com from Ada Lovelace?');
      expect(text).toContain('signs out sessions using this address');
      expect(text).not.toContain('primary');
    });

    // The auth service promotes the alphabetically first remaining address, so
    // the prompt must not simply name the next one in the list.
    it('names the address that will become primary', async () => {
      expect(await promptFor('b@test.com')).toContain('c@test.com becomes the new primary email.');
    });
  });
});
