import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DegradedBanner } from './DegradedBanner';
import { degradedMessage } from '../lib/degraded';
import type { SystemStatus } from '../lib/types';

const base: SystemStatus = {
  vllm: { status: 'running', model: 'qwen3.8-27b' },
  services: {},
  timestamp: '2026-11-12T10:00:00Z',
};

describe('DegradedBanner', () => {
  it('shows nothing in normal operation', () => {
    expect(degradedMessage(base)).toBeNull();
    expect(degradedMessage({ ...base, llm_fallback: { active: false, model: null, since: null } })).toBeNull();
    render(<DegradedBanner status={base} />);
    expect(screen.queryByTestId('degraded-banner')).toBeNull();
  });

  it('names the hosted model and says messages leave the cluster', () => {
    render(<DegradedBanner status={{
      ...base,
      llm_fallback: { active: true, model: 'claude-haiku-5-5', since: '2026-11-12T09:00:00+00:00' },
    }} />);
    const banner = screen.getByTestId('degraded-banner');
    expect(banner.textContent).toContain('claude-haiku-5-5');
    expect(banner.textContent).toContain('sent to Anthropic');
  });

  it('prefers the reduced-mode note from the gateway', () => {
    const msg = degradedMessage({
      ...base,
      llm_fallback: { active: true, model: 'claude-haiku-5-5', since: null },
      backend: { mode: 'reduced', since: '2026-11-12T09:00:00+00:00', history_note: 'Chats from before X are not shown.' },
    });
    expect(msg).toBe('Chats from before X are not shown.');
  });
});
