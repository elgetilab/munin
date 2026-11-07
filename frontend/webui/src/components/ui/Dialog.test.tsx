import { useRef, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Dialog } from './Dialog';
import type { DialogProps } from './Dialog';

function Harness(props: Partial<DialogProps>) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>open</button>
      <button>behind</button>
      {open && (
        <Dialog title="Add user" onClose={() => setOpen(false)} footer={<button>Save</button>} {...props}>
          {props.children ?? <input aria-label="Name" />}
        </Dialog>
      )}
    </>
  );
}

async function openHarness(props: Partial<DialogProps> = {}) {
  const user = userEvent.setup();
  render(<Harness {...props} />);
  await user.click(screen.getByText('open'));
  return user;
}

describe('Dialog', () => {
  it('is a modal dialog labelled by its title', async () => {
    await openHarness();
    const dialog = screen.getByRole('dialog', { name: 'Add user' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('renders outside the React parent so a stacking context cannot cover it', () => {
    const { container } = render(<Dialog title="T" onClose={() => {}}>body</Dialog>);
    expect(container).toBeEmptyDOMElement();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('moves focus to the first field', async () => {
    await openHarness();
    expect(screen.getByLabelText('Name')).toHaveFocus();
  });

  it('honours initialFocusRef', () => {
    function WithRef() {
      const ref = useRef<HTMLButtonElement>(null);
      return (
        <Dialog title="T" onClose={() => {}} initialFocusRef={ref}>
          <input aria-label="first" />
          <button ref={ref}>target</button>
        </Dialog>
      );
    }
    render(<WithRef />);
    expect(screen.getByText('target')).toHaveFocus();
  });

  it('leaves focus on a child that used autoFocus, and still returns it to the opener', async () => {
    function AutoFocus() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>open</button>
          {open && (
            <Dialog title="T" onClose={() => setOpen(false)}>
              <input aria-label="first" />
              <input aria-label="second" autoFocus />
            </Dialog>
          )}
        </>
      );
    }
    const user = userEvent.setup();
    render(<AutoFocus />);
    await user.click(screen.getByText('open'));
    expect(screen.getByLabelText('second')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.getByText('open')).toHaveFocus();
  });

  it('closes on Escape and returns focus to the opener', async () => {
    const user = await openHarness();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('open')).toHaveFocus();
  });

  it('leaves Escape to a control inside that keeps it for itself', async () => {
    const user = await openHarness({
      children: <input aria-label="Name" onKeyDown={e => { if (e.key === 'Escape') e.preventDefault(); }} />,
    });
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('keeps Tab inside, in both directions', async () => {
    const user = await openHarness();
    const name = screen.getByLabelText('Name');
    const save = screen.getByText('Save');
    await user.tab();
    expect(save).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();
    await user.tab({ shift: true });
    expect(save).toHaveFocus();
  });

  it('pulls focus back in if it ended up outside', async () => {
    const user = await openHarness();
    screen.getByText('behind').focus();
    await user.tab();
    expect(screen.getByLabelText('Name')).toHaveFocus();
  });

  it('skips disabled controls when trapping', async () => {
    const user = await openHarness({ footer: <button disabled>Save</button> });
    const name = screen.getByLabelText('Name');
    await user.tab();
    expect(name).toHaveFocus();
  });

  it('focuses the panel itself when nothing inside can take focus', () => {
    render(<Dialog title="T" onClose={() => {}}>just text</Dialog>);
    expect(screen.getByRole('dialog')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('closes on a backdrop click but not on a click inside', async () => {
    const user = await openHarness();
    await user.click(screen.getByText('Add user'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByTestId('dialog-backdrop'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not close when a drag starts inside and ends on the backdrop', async () => {
    await openHarness();
    fireEvent.mouseDown(screen.getByLabelText('Name'));
    fireEvent.click(screen.getByTestId('dialog-backdrop'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('ignores the backdrop with closeOnBackdrop false, but Escape still closes', async () => {
    const user = await openHarness({ closeOnBackdrop: false });
    await user.click(screen.getByTestId('dialog-backdrop'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps phone margins, a capped height and a scrolling body', () => {
    render(<Dialog title="T" onClose={() => {}} footer={<button>Save</button>}>body</Dialog>);
    expect(screen.getByTestId('dialog-backdrop')).toHaveClass('p-4');
    // Dark blurs under a bg-primary veil; light dims with the scrim instead.
    expect(screen.getByTestId('dialog-backdrop')).toHaveClass(
      'bg-bg-primary/70', 'backdrop-blur-sm', '[[data-theme=light]_&]:bg-scrim',
    );
    expect(screen.getByRole('dialog')).toHaveClass('max-h-full', 'flex-col');
    expect(screen.getByText('body')).toHaveClass('overflow-y-auto', 'min-h-0');
    expect(screen.getByText('Save').parentElement).toHaveClass('flex-shrink-0');
  });

  it('closes only the top dialog on Escape, and traps Tab there', async () => {
    function Nested() {
      const [outer, setOuter] = useState(true);
      const [inner, setInner] = useState(false);
      return outer ? (
        <Dialog title="Outer" onClose={() => setOuter(false)}>
          <button onClick={() => setInner(true)}>open inner</button>
          <button>outer other</button>
          {inner && (
            <Dialog title="Inner" onClose={() => setInner(false)}>
              <button>inner only</button>
            </Dialog>
          )}
        </Dialog>
      ) : null;
    }
    const user = userEvent.setup();
    render(<Nested />);
    await user.click(screen.getByText('open inner'));
    expect(screen.getByText('inner only')).toHaveFocus();
    await user.tab();
    expect(screen.getByText('inner only')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Inner' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Outer' })).toBeInTheDocument();
    expect(screen.getByText('open inner')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
