import { render, screen, waitFor } from '@testing-library/react';
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
});
