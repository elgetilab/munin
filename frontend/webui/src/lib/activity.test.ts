import { activityLine, toolPhrase } from './activity';
import type { ToolCall } from './types';

const call = (name: string, result?: unknown): ToolCall => ({ id: name, name, arguments: {}, result } as ToolCall);
const sum = () => '';

describe('activity line', () => {
  it('phrases known tools in both tenses and falls back to the name', () => {
    expect(toolPhrase('run_python', false)).toBe('Running code');
    expect(toolPhrase('run_python', true)).toBe('Ran code');
    expect(toolPhrase('brand_new_tool', true)).toBe('Used brand new tool');
  });

  it('joins steps naturally and shortens long turns', () => {
    const names = ['web_search', 'web_fetch', 'run_python', 'create_artifact'];
    expect(activityLine({ toolCalls: names.slice(0, 3).map(n => call(n, {})), summarise: sum }).text)
      .toBe('Searched the web, read web pages and ran code · 3 steps');
    expect(activityLine({ toolCalls: names.map(n => call(n, {})), summarise: sum }).text)
      .toBe('Searched the web, read web pages and 2 more · 4 steps');
  });

  it('says thinking while it thinks, and how long once done', () => {
    expect(activityLine({ thinking: 'hm', isStreaming: true, summarise: sum })).toMatchObject({ text: 'Thinking…', active: true });
    expect(activityLine({ thinking: 'hm', thinkingDuration: 3200, summarise: sum }).text).toBe('Thought for 3.2s');
  });

  it('names the knowledge base when documents were retrieved', () => {
    const rag = { sources_used: ['papers'], documents: [{ title: 'A', source: 'papers' }] } as never;
    expect(activityLine({ ragContext: rag, toolCalls: [call('paper_search', {})], summarise: () => '2 papers' }).text)
      .toBe('Searched the knowledge base and searched papers · 2 papers');
  });
});
