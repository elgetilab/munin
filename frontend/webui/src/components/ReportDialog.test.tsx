import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { ReportDialog } from './ReportDialog';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';

// The dialog closes itself through the ui store, and it does that from two
// document-level listeners. Those listeners are the reason onClose had to
// become stable, so what they do is worth pinning.

function renderDialog(onReported = vi.fn()) {
  useUiStore.setState({ showReportDialog: true });
  return { onReported, ...render(<ReportDialog conversationId="c1" onReported={onReported} />) };
}

const isOpen = () => useUiStore.getState().showReportDialog;

describe('ReportDialog', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('closes on Escape', async () => {
    renderDialog();
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(isOpen()).toBe(false));
  });

  it('closes on a click outside the panel', async () => {
    renderDialog();
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(isOpen()).toBe(false));
  });

  it('stays open when the click lands inside the panel', async () => {
    renderDialog();
    fireEvent.mouseDown(screen.getByText('Report this chat'));
    expect(isOpen()).toBe(true);
  });

  // Typing re-renders the dialog. With onClose rebuilt on every render, the
  // two effects above tore down and re-subscribed their listeners on each
  // keystroke; this checks they still work after a burst of renders.
  it('still closes on Escape after the reason has been typed into', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.type(screen.getByPlaceholderText('Describe the issue...'), 'something went wrong');
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(isOpen()).toBe(false));
  });

  it('sends the report and tells the parent', async () => {
    const user = userEvent.setup();
    const { onReported } = renderDialog();
    await user.type(screen.getByPlaceholderText('Describe the issue...'), 'bad answer');
    await user.click(screen.getByText('Send report'));
    await waitFor(() => expect(onReported).toHaveBeenCalledTimes(1));
  });

  it('surfaces a failed submission instead of closing', async () => {
    const user = userEvent.setup();
    server.use(http.post('/api/chats/:id/report', () => new HttpResponse(null, { status: 500 })));
    const { onReported } = renderDialog();
    await user.click(screen.getByText('Send report'));
    await waitFor(() => expect(screen.getByText(/failed|error/i)).toBeInTheDocument());
    expect(onReported).not.toHaveBeenCalled();
    expect(isOpen()).toBe(true);
  });
});
