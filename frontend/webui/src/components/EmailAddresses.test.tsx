import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { EmailAddresses } from './EmailAddresses';

const A = 'https://auth.muninai.org/auth/me/emails';

const LIST = {
  emails: [
    { email: 'me@uni.de', primary: true, current: true },
    { email: 'me@home.org', primary: false, current: false },
  ],
  max: 5,
};

beforeEach(() => {
  server.use(
    http.get(A, () => HttpResponse.json(LIST)),
    http.get('https://auth.muninai.org/verify/status', () => HttpResponse.json({ state: 'sent' })),
  );
});

describe('EmailAddresses', () => {
  it('lists addresses and will not remove the one signed in with', async () => {
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    await screen.findByText('me@home.org');
    const removes = screen.getAllByRole('button', { name: 'Remove' });
    expect(removes[0]).toBeDisabled();
    expect(removes[1]).toBeEnabled();
    expect(screen.getByText('signed in')).toBeInTheDocument();
  });

  it('adds an address after the code from that inbox is entered', async () => {
    let confirmed: unknown = null;
    server.use(
      http.post(A, async ({ request }) => {
        const { email } = (await request.json()) as { email: string };
        return HttpResponse.json({ email, send: 's1', expiry_minutes: 10 }, { status: 202 });
      }),
      http.post(`${A}/verify`, async ({ request }) => {
        confirmed = await request.json();
        return HttpResponse.json({ ...LIST, emails: [...LIST.emails, { email: 'new@x.org', primary: false, current: false }] });
      }),
    );
    const user = userEvent.setup();
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    await user.type(await screen.findByPlaceholderText('Add another address'), 'new@x.org');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    expect(await screen.findByText(/sent a 6-digit code to new@x.org/)).toBeInTheDocument();
    expect(screen.getByText(/does not sign you in/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Confirmation code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('new@x.org added. You can now sign in with it.')).toBeInTheDocument();
    expect(confirmed).toEqual({ email: 'new@x.org', code: '123456' });
  });

  it('shows the server reason when an address is refused', async () => {
    server.use(http.post(A, () => HttpResponse.json(
      { error: 'This address cannot be added. If it is yours, contact support.' }, { status: 409 })));
    const user = userEvent.setup();
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    await user.type(await screen.findByPlaceholderText('Add another address'), 'taken@x.org');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be added');
  });

  it('says so when the code mail could not be sent', async () => {
    server.use(
      http.post(A, () => HttpResponse.json({ email: 'n@x.org', send: 's2', expiry_minutes: 10 }, { status: 202 })),
      http.get('https://auth.muninai.org/verify/status', () => HttpResponse.json({ state: 'failed' })),
    );
    const user = userEvent.setup();
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    await user.type(await screen.findByPlaceholderText('Add another address'), 'n@x.org');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    await waitFor(() => expect(screen.getByText(/could not send the email to n@x.org/)).toBeInTheDocument(), { timeout: 4000 });
  });

  it('hides the add form at the cap', async () => {
    server.use(http.get(A, () => HttpResponse.json({ ...LIST, max: 2 })));
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    expect(await screen.findByText('You have the maximum of 2 addresses.')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Add another address')).not.toBeInTheDocument();
  });

  it('falls back to the profile address when the list fails', async () => {
    server.use(http.get(A, () => HttpResponse.json({ error: 'x' }, { status: 500 })));
    render(<EmailAddresses fallbackEmail="me@uni.de" />);
    expect(await screen.findByText(/Could not load your other addresses/)).toBeInTheDocument();
    expect(screen.getByText('me@uni.de')).toBeInTheDocument();
  });
});
