import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { KnowledgePanel } from './KnowledgePanel';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import type { TagCatalog, TagChip } from '../lib/types';

const CATALOG: TagCatalog = {
  topics: [{ slug: 'nmr', label: 'NMR spectroscopy', paper_count: 12 }],
  groups: [
    { slug: 'zeitler', display_name: 'Zeitler Group', paper_count: 30 },
    { slug: 'new', display_name: 'New Lab', paper_count: 0 },
  ],
  contributors: [
    { username: 'ada', display_name: 'Ada Lovelace', group_slug: 'zeitler', paper_count: 5 },
    { username: 'bob', display_name: 'Bob', group_slug: 'zeitler', paper_count: 0 },
  ],
  contributor_count: 2,
};

function renderPanel(activeTags: TagChip[] = [], catalog = CATALOG) {
  const onTagsChange = vi.fn();
  render(<KnowledgePanel catalog={catalog} activeTags={activeTags} onTagsChange={onTagsChange} />);
  return onTagsChange;
}

function stubBelowXl(matches: boolean) {
  window.matchMedia = vi.fn(() => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
}

describe('KnowledgePanel', () => {
  beforeEach(() => {
    _resetUiStoreForTests();
    useUiStore.setState({ knowledgePanelOpen: true });
    window.history.replaceState(null, '', '/');
  });

  it('says what selecting a row does', () => {
    renderPanel();
    expect(screen.getByText(/limit what Munin searches.*Numbers are paper counts/)).toBeInTheDocument();
  });

  it('exposes which rows are selected, with a mark as well as a colour', () => {
    renderPanel([{ kind: 'group', value: 'zeitler' }]);
    const row = screen.getByRole('button', { name: /^Zeitler Group/ });
    expect(row).toHaveAttribute('aria-pressed', 'true');
    expect(within(row).getByTestId('tag-check')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New Lab/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('exposes whether a section is open', async () => {
    renderPanel();
    const section = screen.getByRole('button', { name: 'Topics (1)' });
    expect(section).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(section);
    expect(section).toHaveAttribute('aria-expanded', 'true');
  });

  it('really disables an empty row and says why', async () => {
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Contributors (2)' }));
    for (const name of [/New Lab/, /Bob/]) {
      const row = screen.getByRole('button', { name });
      expect(row).toBeDisabled();
      expect(row).toHaveAttribute('title', 'No papers uploaded yet');
      expect(row).not.toHaveClass('cursor-pointer');
    }
  });

  it('toggles a tag', async () => {
    const onTagsChange = renderPanel();
    await userEvent.click(screen.getByRole('button', { name: /^Zeitler Group/ }));
    expect(onTagsChange).toHaveBeenCalledWith([{ kind: 'group', value: 'zeitler' }]);
  });

  it('shows filters by name and removes them by name', async () => {
    const tags: TagChip[] = [
      { kind: 'group', value: 'zeitler' },
      { kind: 'contributor', value: 'ada' },
      { kind: 'topic', value: 'nmr' },
      { kind: 'topic', value: 'gone' },
    ];
    const onTagsChange = renderPanel(tags);
    for (const name of ['Zeitler Group', 'Ada Lovelace', 'NMR spectroscopy', 'gone']) {
      expect(screen.getByRole('button', { name: `Remove filter ${name}` })).toBeInTheDocument();
    }
    expect(screen.queryByText(/#/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Remove filter Ada Lovelace' }));
    expect(onTagsChange).toHaveBeenCalledWith(tags.filter(t => t.value !== 'ada'));
  });

  it('gives a filter a remove target of at least 24px', () => {
    renderPanel([{ kind: 'group', value: 'zeitler' }]);
    expect(screen.getByRole('button', { name: 'Remove filter Zeitler Group' })).toHaveClass('w-6', 'h-6');
  });

  it('leads to the Knowledge page', async () => {
    renderPanel();
    await userEvent.click(screen.getByRole('link', { name: 'Browse all papers' }));
    expect(useUiStore.getState().showKnowledgePage).toBe(true);
    expect(useUiStore.getState().knowledgePanelOpen).toBe(false);
    expect(window.location.pathname).toBe('/knowledge');
  });

  it('says so once when nothing has been uploaded', () => {
    renderPanel([], { topics: [], groups: [], contributors: [], contributor_count: 0 });
    expect(screen.getByText('Nothing uploaded yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Research Groups/ })).not.toBeInTheDocument();
  });

  describe('topics', () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ slug: `t${i}`, label: i === 24 ? 'Cryo-EM' : `Topic ${i}`, paper_count: 1 }));

    it('offers a filter only for a long list, and it reaches every topic', async () => {
      const user = userEvent.setup();
      renderPanel([], { ...CATALOG, topics: many });
      await user.click(screen.getByRole('button', { name: 'Topics (25)' }));
      expect(screen.queryByRole('button', { name: /Cryo-EM/ })).not.toBeInTheDocument();

      await user.type(screen.getByRole('textbox', { name: 'Filter topics' }), 'cryo');
      expect(screen.getByRole('button', { name: /Cryo-EM/ })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Topic 0/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument();
    });

    it('has no filter for a short list', async () => {
      renderPanel();
      await userEvent.click(screen.getByRole('button', { name: 'Topics (1)' }));
      expect(screen.queryByRole('textbox', { name: 'Filter topics' })).not.toBeInTheDocument();
    });
  });

  describe('as a modal below xl', () => {
    afterEach(() => {
      delete (window as Partial<Window>).matchMedia;
    });

    it('is a labelled dialog with focus on Close, Escape closes it and focus goes back', async () => {
      stubBelowXl(true);
      const opener = document.createElement('button');
      document.body.append(opener);
      opener.focus();
      const { unmount } = render(<KnowledgePanel catalog={CATALOG} activeTags={[]} onTagsChange={vi.fn()} />);

      expect(screen.getByRole('dialog', { name: 'Available Knowledge' })).toBe(screen.getByTestId('knowledge-panel'));
      expect(screen.getByRole('button', { name: 'Close knowledge panel' })).toHaveFocus();

      await userEvent.keyboard('{Escape}');
      expect(useUiStore.getState().knowledgePanelOpen).toBe(false);

      unmount();
      expect(opener).toHaveFocus();
      opener.remove();
    });

    it('closes from Done', async () => {
      stubBelowXl(true);
      renderPanel();
      await userEvent.click(screen.getByRole('button', { name: 'Done' }));
      expect(useUiStore.getState().knowledgePanelOpen).toBe(false);
    });

    it('is an ordinary column from xl up', () => {
      stubBelowXl(false);
      renderPanel();
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.getByTestId('knowledge-panel')).not.toHaveAttribute('tabindex');
    });
  });
});
