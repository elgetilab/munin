import { useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useBelowXl, useModal } from './useModal';

// A panel shaped like KnowledgePanel: full-screen overlay below xl, side column above.
function Panel({ onClose }: { onClose: () => void }) {
  const modal = useModal({ onClose, active: useBelowXl() });
  return (
    <div data-testid="panel" {...modal.panelProps}>
      <h3 id={modal.titleId}>Knowledge</h3>
      <button onClick={onClose}>close</button>
      <button>other</button>
    </div>
  );
}

function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>open</button>
      {open && <Panel onClose={() => setOpen(false)} />}
    </>
  );
}

function mockViewport(belowXl: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    get matches() { return belowXl; },
    addEventListener: (_: string, l: () => void) => listeners.add(l),
    removeEventListener: (_: string, l: () => void) => listeners.delete(l),
  };
  vi.stubGlobal('matchMedia', vi.fn(() => query));
  return {
    resize(next: boolean) {
      belowXl = next;
      act(() => listeners.forEach(l => l()));
    },
  };
}

describe('useModal on a panel that is only modal below xl', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('asks about the xl breakpoint', () => {
    mockViewport(true);
    render(<Panel onClose={() => {}} />);
    expect(window.matchMedia).toHaveBeenCalledWith('(width < 80rem)');
  });

  it('below xl it is a labelled dialog that takes focus, traps and returns it', async () => {
    mockViewport(true);
    const user = userEvent.setup();
    render(<Page />);
    await user.click(screen.getByText('open'));

    const panel = screen.getByRole('dialog', { name: 'Knowledge' });
    expect(panel).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByText('close')).toHaveFocus();

    await user.tab();
    await user.tab();
    expect(screen.getByText('close')).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('panel')).not.toBeInTheDocument();
    expect(screen.getByText('open')).toHaveFocus();
  });

  it('from xl up it is an ordinary column: no role, no focus move, Escape does nothing', async () => {
    mockViewport(false);
    const user = userEvent.setup();
    render(<Page />);
    await user.click(screen.getByText('open'));

    const panel = screen.getByTestId('panel');
    expect(panel).not.toHaveAttribute('role');
    expect(panel).not.toHaveAttribute('aria-modal');
    expect(screen.getByText('open')).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.getByTestId('panel')).toBeInTheDocument();
  });

  it('follows the viewport when it is resized across the breakpoint', async () => {
    const viewport = mockViewport(false);
    const user = userEvent.setup();
    render(<Page />);
    await user.click(screen.getByText('open'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    viewport.resize(true);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('close')).toHaveFocus();

    viewport.resize(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('is an ordinary column where matchMedia does not exist', () => {
    vi.stubGlobal('matchMedia', undefined);
    render(<Panel onClose={() => {}} />);
    expect(screen.getByTestId('panel')).not.toHaveAttribute('role');
  });

  it('does not pull focus back when the parent re-renders with a new onClose', async () => {
    mockViewport(true);
    const user = userEvent.setup();
    const { rerender } = render(<Panel onClose={() => {}} />);
    await user.tab();
    expect(screen.getByText('other')).toHaveFocus();
    const latest = vi.fn();
    rerender(<Panel onClose={latest} />);
    expect(screen.getByText('other')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(latest).toHaveBeenCalledTimes(1);
  });
});
