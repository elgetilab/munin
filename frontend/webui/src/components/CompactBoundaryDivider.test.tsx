import { fireEvent, render, screen } from '@testing-library/react';
import { CompactBoundaryDivider } from './CompactBoundaryDivider';
import type { CompactBoundary } from '../lib/types';

function boundary(over: Partial<CompactBoundary> = {}): CompactBoundary {
  return {
    summary_through_index: 3,
    dropped_messages: 4,
    summary: 'The user asked about CRISPR off-target effects.',
    is_fresh: false,
    ...over,
  };
}

describe('CompactBoundaryDivider', () => {
  it('is named by its visible text and reports its open state', () => {
    render(<CompactBoundaryDivider boundary={boundary()} />);
    const button = screen.getByRole('button');
    expect(button).not.toHaveAttribute('aria-label');
    expect(button).toHaveAccessibleName(/4 earlier messages summarised\. Show summary/i);
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).toHaveTextContent('\u25B8');

    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(button).toHaveAccessibleName(/Hide summary/i);
    expect(button).toHaveTextContent('\u25BE');
  });

  it('uses the singular for one message', () => {
    render(<CompactBoundaryDivider boundary={boundary({ dropped_messages: 1 })} />);
    const button = screen.getByRole('button');
    expect(button).toHaveTextContent('1 earlier message summarised.');
    expect(button).not.toHaveTextContent('1 earlier messages');
  });

  it('keeps the opened summary selectable', () => {
    const { container } = render(<CompactBoundaryDivider boundary={boundary()} />);
    const button = screen.getByRole('button');
    fireEvent.click(button);
    const summary = screen.getByText(/CRISPR off-target/);
    expect(container.querySelectorAll('.select-none')).toHaveLength(1);
    expect(button).toHaveClass('select-none');
    expect(summary.closest('.select-none')).toBeNull();
  });
});
