import { render, screen } from '@testing-library/react';
import { SleepingPage } from './SleepingPage';

const HOUR = 60 * 60 * 1000;

// createVortex is a global loaded from a plain <script> in production.
beforeAll(() => {
  (globalThis as unknown as { createVortex: unknown }).createVortex = () => () => {};
});

function expectOutageCopy() {
  expect(screen.getByRole('heading', { name: 'Chat is temporarily offline' })).toBeInTheDocument();
  expect(screen.queryByText(/resting/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/reserved for experiments/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/will be back/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
}

describe('SleepingPage', () => {
  it('states the window in viewer time when the next start is within it', () => {
    const end = new Date(Date.now() + 2 * HOUR);
    const time = (d: Date) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    render(<SleepingPage nextStart={end.toISOString()} />);

    expect(screen.getByRole('heading', { name: 'Munin is resting' })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`between ${time(new Date(end.getTime() - 4 * HOUR))} and ${time(end)}`))).toBeInTheDocument();
    expect(screen.getByText(/will be back/)).toHaveTextContent(time(end));
  });

  it('promises no return time for an outage hours before the next start', () => {
    render(<SleepingPage nextStart={new Date(Date.now() + 15 * HOUR).toISOString()} />);
    expectOutageCopy();
  });

  it.each([
    ['missing', undefined],
    ['invalid', 'not-a-date'],
    ['already past', new Date(Date.now() - HOUR).toISOString()],
  ])('promises no return time when the next start is %s', (_name, nextStart) => {
    render(<SleepingPage nextStart={nextStart} />);
    expectOutageCopy();
  });
});
