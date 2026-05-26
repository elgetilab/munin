import { render, screen } from '@testing-library/react';
import { MaintenancePage } from './MaintenancePage';

// createVortex is a global loaded from a plain <script> in production;
// stub it so the component's useEffect doesn't throw under jsdom.
beforeAll(() => {
  (globalThis as unknown as { createVortex: unknown }).createVortex = () => () => {};
});

describe('MaintenancePage', () => {
  it('always shows the maintenance heading', () => {
    render(<MaintenancePage />);
    expect(
      screen.getByRole('heading', { name: /under maintenance/i }),
    ).toBeInTheDocument();
  });

  it('renders the operator-set message when provided', () => {
    render(<MaintenancePage message="Running NTL9 experiments, back Wednesday" />);
    expect(
      screen.getByText('Running NTL9 experiments, back Wednesday'),
    ).toBeInTheDocument();
  });

  it('falls back to generic copy when no message is given', () => {
    render(<MaintenancePage />);
    expect(
      screen.getByText(/temporarily unavailable while the cluster is being worked on/i),
    ).toBeInTheDocument();
  });

  it('shows a "started" line when a valid since timestamp is given', () => {
    render(<MaintenancePage since="2026-05-22T14:30:00Z" />);
    expect(screen.getByText(/Maintenance started/i)).toBeInTheDocument();
  });

  it('omits the "started" line when since is absent or unparseable', () => {
    const { rerender } = render(<MaintenancePage />);
    expect(screen.queryByText(/Maintenance started/i)).not.toBeInTheDocument();
    rerender(<MaintenancePage since="not-a-date" />);
    expect(screen.queryByText(/Maintenance started/i)).not.toBeInTheDocument();
  });
});
