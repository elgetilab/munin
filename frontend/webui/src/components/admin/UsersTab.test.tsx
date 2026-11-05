import { render, screen, waitFor, within } from '@testing-library/react';
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
      screen.getByPlaceholderText('Filter by first/last name, email, or group...'),
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
});
