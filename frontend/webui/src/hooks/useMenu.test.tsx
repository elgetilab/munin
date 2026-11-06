import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Dialog } from '../components/ui/Dialog';
import { useMenu } from './useMenu';
import type { UseMenuOptions } from './useMenu';

function Menu(options: UseMenuOptions) {
  const menu = useMenu(options);
  return (
    <>
      <button {...menu.triggerProps}>trigger</button>
      <button>after</button>
      {menu.open && (
        <div {...menu.menuProps}>
          <button role="menuitem" onClick={menu.close}>one</button>
          <button role="menuitem" disabled>skipped</button>
          <button role="menuitem">two</button>
          <button role="menuitem">three</button>
        </div>
      )}
    </>
  );
}

function Popover() {
  const menu = useMenu({ kind: 'popover' });
  return (
    <>
      <button {...menu.triggerProps}>trigger</button>
      <button>after</button>
      {menu.open && (
        <div aria-label="Filters" {...menu.menuProps}>
          <input aria-label="Field" />
        </div>
      )}
    </>
  );
}

describe('useMenu', () => {
  it('gives the trigger and the menu their semantics', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    const trigger = screen.getByText('trigger');
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('toggles from the trigger without the outside handler reopening it', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('trigger'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('moves focus to the first enabled item on open', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    expect(screen.getByText('one')).toHaveFocus();
  });

  describe('with items hidden by CSS', () => {
    // jsdom applies no CSS, so this stands in for a `lg:hidden` item.
    function HiddenItems() {
      const menu = useMenu();
      return (
        <>
          <button {...menu.triggerProps}>trigger</button>
          {menu.open && (
            <div {...menu.menuProps}>
              <button role="menuitem" data-hidden>first hidden</button>
              <button role="menuitem">one</button>
              <button role="menuitem" data-hidden>middle hidden</button>
              <button role="menuitem">two</button>
              <button role="menuitem" data-hidden>last hidden</button>
            </div>
          )}
        </>
      );
    }

    beforeEach(() => {
      HTMLElement.prototype.checkVisibility = function (this: HTMLElement) {
        return !this.hasAttribute('data-hidden');
      };
    });
    afterEach(() => {
      delete (HTMLElement.prototype as Partial<HTMLElement>).checkVisibility;
    });

    it('opens on the first visible item', async () => {
      const user = userEvent.setup();
      render(<HiddenItems />);
      await user.click(screen.getByText('trigger'));
      expect(screen.getByText('one')).toHaveFocus();
    });

    it('moves past them with every key', async () => {
      const user = userEvent.setup();
      render(<HiddenItems />);
      await user.click(screen.getByText('trigger'));

      await user.keyboard('{ArrowDown}');
      expect(screen.getByText('two')).toHaveFocus();
      await user.keyboard('{ArrowDown}');
      expect(screen.getByText('one')).toHaveFocus();
      await user.keyboard('{ArrowUp}');
      expect(screen.getByText('two')).toHaveFocus();
      await user.keyboard('{Home}');
      expect(screen.getByText('one')).toHaveFocus();
      await user.keyboard('{End}');
      expect(screen.getByText('two')).toHaveFocus();
    });
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByText('trigger')).toHaveFocus();
  });

  it('closes on an outside click without taking focus back', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('after'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByText('after')).toHaveFocus();
  });

  it('sees an outside click even when the target stops propagation', async () => {
    const user = userEvent.setup();
    render(
      <>
        <Menu />
        <button onMouseDown={e => e.stopPropagation()}>row</button>
      </>,
    );
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('row'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('closes on an outside tap', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    fireEvent.touchStart(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('closes when focus moves elsewhere', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    fireEvent.focusIn(screen.getByText('after'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('stays open on a click inside', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('two'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('close() closes and returns focus', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('one'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByText('trigger')).toHaveFocus();
  });

  it('moves with arrow keys, wrapping and skipping disabled items, plus Home and End', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    await user.keyboard('{ArrowDown}');
    expect(screen.getByText('two')).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByText('three')).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByText('one')).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(screen.getByText('three')).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByText('one')).toHaveFocus();
  });

  // user-event works out Tab's destination from the element the key was pressed
  // on, where a browser starts from whatever has focus once keydown has run. So
  // this checks what the browser needs: focus on the trigger, default not stopped.
  it('Tab closes the menu with focus on the trigger and leaves the browser to move on', async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByText('trigger'));
    const notCancelled = fireEvent.keyDown(screen.getByText('one'), { key: 'Tab' });
    expect(notCancelled).toBe(true);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByText('trigger')).toHaveFocus();
  });

  it('can be controlled by the parent', async () => {
    function Controlled() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <span data-testid="state">{String(open)}</span>
          <Menu open={open} onOpenChange={setOpen} />
        </>
      );
    }
    const user = userEvent.setup();
    render(<Controlled />);
    await user.click(screen.getByText('trigger'));
    expect(screen.getByTestId('state')).toHaveTextContent('true');
    await user.keyboard('{Escape}');
    expect(screen.getByTestId('state')).toHaveTextContent('false');
  });

  it('stays closed when the parent says so, even after a trigger click', async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<Menu open={false} onOpenChange={onOpenChange} />);
    await user.click(screen.getByText('trigger'));
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('as a popover it is a dialog that leaves focus and arrow keys alone', async () => {
    const user = userEvent.setup();
    render(<Popover />);
    const trigger = screen.getByText('trigger');
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Filters' })).toBeInTheDocument();
    expect(trigger).toHaveFocus();
    await user.click(screen.getByLabelText('Field'));
    await user.keyboard('{ArrowDown}');
    expect(screen.getByLabelText('Field')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('inside a Dialog, Escape closes the menu first and the Dialog second', async () => {
    function InDialog() {
      const [open, setOpen] = useState(true);
      return open ? <Dialog title="T" onClose={() => setOpen(false)}><Menu /></Dialog> : null;
    }
    const user = userEvent.setup();
    render(<InDialog />);
    await user.click(screen.getByText('trigger'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('hands focus through an item that opens a Dialog, and back to the trigger', async () => {
    function ItemOpensDialog() {
      const menu = useMenu();
      const [dialog, setDialog] = useState(false);
      return (
        <>
          <button {...menu.triggerProps}>trigger</button>
          {menu.open && (
            <div {...menu.menuProps}>
              <button role="menuitem" onClick={() => { menu.close(); setDialog(true); }}>rename</button>
            </div>
          )}
          {dialog && (
            <Dialog title="Rename" onClose={() => setDialog(false)}>
              <input aria-label="Name" />
            </Dialog>
          )}
        </>
      );
    }
    const user = userEvent.setup();
    render(<ItemOpensDialog />);
    await user.click(screen.getByText('trigger'));
    await user.click(screen.getByText('rename'));
    expect(screen.getByLabelText('Name')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.getByText('trigger')).toHaveFocus();
  });
});
