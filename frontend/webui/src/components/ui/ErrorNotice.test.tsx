import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ErrorNotice } from './ErrorNotice';

describe('ErrorNotice', () => {
  it('announces the message and offers nothing it was not asked for', () => {
    render(<ErrorNotice message="Could not load users." />);
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load users.');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('calls onRetry from the Retry button', async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorNotice message="x" onRetry={onRetry} />);
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('calls onDismiss from the dismiss button', async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    render(<ErrorNotice message="x" onDismiss={onDismiss} />);
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('does not submit a surrounding form', async () => {
    const onSubmit = vi.fn(e => e.preventDefault());
    const user = userEvent.setup();
    render(
      <form onSubmit={onSubmit}>
        <ErrorNotice message="x" onRetry={() => {}} onDismiss={() => {}} />
      </form>,
    );
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('adds className for spacing', () => {
    render(<ErrorNotice message="x" className="mt-3" />);
    expect(screen.getByRole('alert')).toHaveClass('mt-3');
  });
});
