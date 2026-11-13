import { useRef, useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './ConfirmDialog';

function Harness({ onConfirm, withReturn = false }: { onConfirm: (close: () => void) => unknown; withReturn?: boolean }) {
  const [open, setOpen] = useState(false);
  const returnRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button onClick={() => setOpen(true)}>open</button>
      <button ref={returnRef}>elsewhere</button>
      {open && (
        <ConfirmDialog
          title="Delete thing?"
          confirmLabel="Delete thing"
          onConfirm={() => onConfirm(() => setOpen(false))}
          onCancel={() => setOpen(false)}
          returnFocusRef={withReturn ? returnRef : undefined}
        >
          It cannot be brought back.
        </ConfirmDialog>
      )}
    </>
  );
}

describe('ConfirmDialog', () => {
  it('is an alertdialog named by its title and described by its message, with focus on Cancel', async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={() => {}} />);
    await user.click(screen.getByText('open'));

    const dialog = screen.getByRole('alertdialog', { name: 'Delete thing?' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleDescription('It cannot be brought back.');
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('Escape and Cancel close it without confirming, and focus goes back to the opener', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    await user.click(screen.getByText('open'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByText('open')).toHaveFocus();

    await user.click(screen.getByText('open'));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByText('open')).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('while the action runs, says so and ignores Cancel, Escape and a second press', async () => {
    const user = userEvent.setup();
    let finish = () => {};
    const onConfirm = vi.fn((close: () => void) => new Promise<void>(resolve => {
      finish = () => { close(); resolve(); };
    }));
    render(<Harness onConfirm={onConfirm} />);
    await user.click(screen.getByText('open'));

    await user.click(screen.getByRole('button', { name: 'Delete thing' }));
    const busy = screen.getByRole('button', { name: 'Deleting...' });
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    await user.click(busy);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(onConfirm).toHaveBeenCalledTimes(1);

    finish();
    await screen.findByText('open');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('sends focus to returnFocusRef on close, when one is given', async () => {
    const user = userEvent.setup();
    render(<Harness withReturn onConfirm={close => close()} />);
    await user.click(screen.getByText('open'));
    await user.click(screen.getByRole('button', { name: 'Delete thing' }));

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByText('elsewhere')).toHaveFocus();
  });
});
