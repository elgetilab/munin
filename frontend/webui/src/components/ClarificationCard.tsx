import { useState } from 'react';
import type { Clarification } from '../lib/types';

interface ClarificationCardProps {
  clarification: Clarification;
  onSubmit: (answer: string) => void;
  disabled?: boolean;
}

export function ClarificationCard({ clarification, onSubmit, disabled }: ClarificationCardProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);

  const setAnswer = (qId: string, value: string) => {
    setAnswers(prev => ({ ...prev, [qId]: value }));
    // Clear custom input when a predefined option is selected
    if (value !== '__custom__') {
      setCustomInputs(prev => ({ ...prev, [qId]: '' }));
    }
  };

  const handleSubmit = () => {
    // Format each answered question as a Q/A pair so the user's
    // reply bubble (and the model's next-turn context) carries the
    // question text alongside the user's chosen answer. Pairs are
    // separated by blank lines so Markdown renders them as distinct
    // paragraphs.
    const pairs = clarification.questions.map((q, i) => {
      const val = answers[q.id];
      const display = val === '__custom__' ? customInputs[q.id] || '' : val || '';
      return `Q${i + 1}: ${q.text}\nA${i + 1}: ${display}`;
    });
    setSubmitted(true);
    onSubmit(pairs.join('\n\n'));
  };

  const allAnswered = clarification.questions.every(q => {
    const val = answers[q.id];
    if (!val) return false;
    if (val === '__custom__') return (customInputs[q.id] || '').trim().length > 0;
    return true;
  });

  const isDisabled = disabled || submitted;

  return (
    <div className="my-3 p-4 bg-bg-secondary border border-border rounded-lg">
      <p className="text-sm font-medium text-text-primary mb-1">Just to make sure I understand:</p>
      {clarification.what_i_understood && (
        <p className="text-sm text-text-secondary italic mb-4 pl-3 border-l-2 border-accent/40">
          {clarification.what_i_understood}
        </p>
      )}

      <div className="space-y-4">
        {clarification.questions.map((q, i) => (
          <div key={q.id}>
            <p className="text-sm text-text-primary mb-2">
              {i + 1}. {q.text}
            </p>
            <div className="flex flex-wrap gap-2">
              {q.options.map(opt => (
                <button
                  key={opt}
                  onClick={() => setAnswer(q.id, opt)}
                  disabled={isDisabled}
                  className={`px-3 py-1.5 rounded-lg text-xs transition-colors cursor-pointer disabled:cursor-default ${
                    answers[q.id] === opt
                      ? 'bg-accent text-bg-primary'
                      : 'bg-bg-tertiary text-text-secondary border border-border hover:border-accent'
                  }`}
                >
                  {opt}
                </button>
              ))}
              {q.allow_custom && (
                <button
                  onClick={() => setAnswer(q.id, '__custom__')}
                  disabled={isDisabled}
                  className={`px-3 py-1.5 rounded-lg text-xs transition-colors cursor-pointer disabled:cursor-default ${
                    answers[q.id] === '__custom__'
                      ? 'bg-accent text-bg-primary'
                      : 'bg-bg-tertiary text-text-secondary border border-border hover:border-accent'
                  }`}
                >
                  Other...
                </button>
              )}
            </div>
            {answers[q.id] === '__custom__' && (
              <input
                type="text"
                value={customInputs[q.id] || ''}
                onChange={e => setCustomInputs(prev => ({ ...prev, [q.id]: e.target.value }))}
                placeholder="Type your answer..."
                disabled={isDisabled}
                className="mt-2 w-full px-3 py-1.5 bg-bg-tertiary border border-border rounded-lg text-xs text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors"
              />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-3">
        {!submitted ? (
          <button
            onClick={handleSubmit}
            disabled={!allAnswered || isDisabled}
            className="px-4 py-1.5 bg-accent text-bg-primary rounded-lg text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-default"
          >
            Submit
          </button>
        ) : (
          <span className="text-xs text-text-secondary">Answered</span>
        )}
      </div>
    </div>
  );
}
