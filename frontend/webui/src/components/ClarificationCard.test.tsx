import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClarificationCard } from './ClarificationCard';
import type { Clarification } from '../lib/types';

const baseClarification: Clarification = {
  tool_call_id: 'tc1',
  conversation_id: 'conv1',
  what_i_understood: 'You want to analyse a dataset.',
  questions: [
    { id: 'q1', text: 'Which dataset?', options: ['MNIST', 'CIFAR-10'], allow_custom: false },
    { id: 'q2', text: 'Which metric?', options: ['Accuracy', 'F1'], allow_custom: false },
  ],
};

describe('ClarificationCard', () => {
  it('renders all questions and their options', () => {
    render(<ClarificationCard clarification={baseClarification} onSubmit={vi.fn()} />);

    expect(screen.getByText(/Which dataset\?/)).toBeInTheDocument();
    expect(screen.getByText(/Which metric\?/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'MNIST' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'CIFAR-10' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accuracy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'F1' })).toBeInTheDocument();
  });

  it('clicking an option selects it (visual feedback via aria / style change)', async () => {
    const user = userEvent.setup();
    render(<ClarificationCard clarification={baseClarification} onSubmit={vi.fn()} />);

    const mnistBtn = screen.getByRole('button', { name: 'MNIST' });
    await user.click(mnistBtn);

    // After clicking, the button gets the accent background class
    expect(mnistBtn.className).toContain('bg-accent');
    // The other option should not have the accent class
    expect(screen.getByRole('button', { name: 'CIFAR-10' }).className).not.toContain('bg-accent text-bg-primary');
  });

  it('submit button is disabled until all questions are answered', async () => {
    const user = userEvent.setup();
    render(<ClarificationCard clarification={baseClarification} onSubmit={vi.fn()} />);

    const submitBtn = screen.getByRole('button', { name: 'Submit' });
    expect(submitBtn).toBeDisabled();

    // Answer only the first question
    await user.click(screen.getByRole('button', { name: 'MNIST' }));
    expect(submitBtn).toBeDisabled();

    // Answer the second question
    await user.click(screen.getByRole('button', { name: 'F1' }));
    expect(submitBtn).toBeEnabled();
  });

  it('with allow_custom, clicking "Other..." shows a text input', async () => {
    const user = userEvent.setup();
    const clarification: Clarification = {
      ...baseClarification,
      questions: [
        { id: 'q1', text: 'Which dataset?', options: ['MNIST'], allow_custom: true },
      ],
    };

    render(<ClarificationCard clarification={clarification} onSubmit={vi.fn()} />);

    expect(screen.queryByPlaceholderText('Type your answer...')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Other...' }));

    expect(screen.getByPlaceholderText('Type your answer...')).toBeInTheDocument();
  });

  it('onSubmit fires with Q/A pair format separated by blank lines', async () => {
    // 2026-06-02: extended from "Q1: <answer>" to "Q1: <question>\nA1:
    // <answer>" so the user-reply bubble carries the question text
    // alongside the chosen answer. Pairs are joined with a blank line
    // so Markdown renders them as separate paragraphs.
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={baseClarification} onSubmit={onSubmit} />);

    await user.click(screen.getByRole('button', { name: 'CIFAR-10' }));
    await user.click(screen.getByRole('button', { name: 'Accuracy' }));
    await user.click(screen.getByRole('button', { name: 'Submit' }));

    expect(onSubmit).toHaveBeenCalledWith(
      'Q1: Which dataset?\nA1: CIFAR-10\n\nQ2: Which metric?\nA2: Accuracy'
    );
  });

  it('after submit, controls are disabled and "Answered" shows', async () => {
    const user = userEvent.setup();
    render(<ClarificationCard clarification={baseClarification} onSubmit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'MNIST' }));
    await user.click(screen.getByRole('button', { name: 'F1' }));
    await user.click(screen.getByRole('button', { name: 'Submit' }));

    expect(screen.getByText('Answered')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Submit' })).not.toBeInTheDocument();

    // Option buttons should be disabled
    expect(screen.getByRole('button', { name: 'MNIST' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'F1' })).toBeDisabled();
  });

  it('submits custom text when "Other..." is used', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const clarification: Clarification = {
      ...baseClarification,
      questions: [
        { id: 'q1', text: 'Which dataset?', options: ['MNIST'], allow_custom: true },
      ],
    };

    render(<ClarificationCard clarification={clarification} onSubmit={onSubmit} />);

    await user.click(screen.getByRole('button', { name: 'Other...' }));

    const input = screen.getByPlaceholderText('Type your answer...');
    await user.type(input, 'ImageNet');

    await user.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onSubmit).toHaveBeenCalledWith('Q1: Which dataset?\nA1: ImageNet');
  });
});
