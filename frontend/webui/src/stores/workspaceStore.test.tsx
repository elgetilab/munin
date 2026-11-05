import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportDialog } from '../components/ReportDialog';
import { useUiStore, _resetUiStoreForTests } from './uiStore';
import { useWorkspaceStore, _resetWorkspaceStoreForTests } from './workspaceStore';

const reported = () => useWorkspaceStore.getState().reportedChats;

describe('addReportedChat', () => {
  beforeEach(() => {
    localStorage.clear();
    _resetWorkspaceStoreForTests();
    _resetUiStoreForTests();
  });

  it('adds to the set without mutating the previous one', () => {
    const before = reported();
    useWorkspaceStore.getState().addReportedChat('c1');
    expect(reported().has('c1')).toBe(true);
    expect(reported()).not.toBe(before);
    expect(before.has('c1')).toBe(false);
  });

  it('keeps working after a second report and another state change', () => {
    const { addReportedChat, setActiveProjectId } = useWorkspaceStore.getState();
    addReportedChat('c1');
    setActiveProjectId('p1');
    addReportedChat('c2');
    expect([...reported()]).toEqual(['c1', 'c2']);
  });

  it('persists the set as a plain array that rehydrates to a Set', async () => {
    useWorkspaceStore.getState().addReportedChat('c1');
    const stored = JSON.parse(localStorage.getItem('munin-workspace') as string);
    expect(stored.state.reportedChats).toEqual(['c1']);

    _resetWorkspaceStoreForTests();
    localStorage.setItem('munin-workspace', JSON.stringify(stored));
    await useWorkspaceStore.persist.rehydrate();
    expect(reported()).toBeInstanceOf(Set);
    expect(reported().has('c1')).toBe(true);
  });

  // App.tsx's onReported closes the dialog, records the chat, then shows the
  // toast, in that order. A throw from the store used to skip the toast and the
  // Reported pill, so this runs the same sequence against the real store.
  it('reaches the toast after a real report goes through the dialog', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ showReportDialog: true });
    const setToast = vi.fn();
    render(
      <ReportDialog
        conversationId="c1"
        onReported={() => {
          useUiStore.getState().setShowReportDialog(false);
          useWorkspaceStore.getState().addReportedChat('c1');
          setToast('reported');
        }}
      />,
    );
    await user.click(screen.getByText('Send report'));
    await waitFor(() => expect(setToast).toHaveBeenCalledTimes(1));
    expect(reported().has('c1')).toBe(true);
    expect(useUiStore.getState().showReportDialog).toBe(false);
  });
});
