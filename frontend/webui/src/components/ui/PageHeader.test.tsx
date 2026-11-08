import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PageHeader } from './PageHeader';
import { CloseIcon, SearchIcon } from './icons';

describe('PageHeader', () => {
  it('shows the title and goes back with one labelled button', async () => {
    const onBack = vi.fn();
    render(<PageHeader title="Settings" onBack={onBack} className="mb-8" />);
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    const back = screen.getByRole('button', { name: 'Back to chat' });
    expect(back).toHaveAttribute('type', 'button');
    await userEvent.click(back);
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

describe('shared icons', () => {
  it('are hidden from screen readers and take a size, stroke and class', () => {
    const { container } = render(<><SearchIcon size={18} className="text-accent" /><CloseIcon /></>);
    const [search, close] = Array.from(container.querySelectorAll('svg'));
    expect(search).toHaveAttribute('aria-hidden', 'true');
    expect(search).toHaveAttribute('width', '18');
    expect(search).toHaveAttribute('stroke-width', '2');
    expect(search).toHaveClass('text-accent');
    expect(close).toHaveAttribute('width', '12');
    expect(close).toHaveAttribute('stroke-width', '2.5');
  });
});
