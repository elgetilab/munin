import { render, screen, waitFor } from '@testing-library/react';
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
});
