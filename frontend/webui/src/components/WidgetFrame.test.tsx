import { render, screen } from '@testing-library/react';
import { WidgetFrame } from './WidgetFrame';
import { stubWidgetFrame } from '../test/widgetFrame';

describe('WidgetFrame', () => {
  it('grants scripts and nothing else', () => {
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} />);
    expect(screen.getByTestId('widget-frame')).toHaveAttribute('sandbox', 'allow-scripts');
  });

  it('sends the state only once the widget is ready, then on each change', () => {
    const { rerender } = render(<WidgetFrame srcDoc="<p></p>" title="w" state={{ n: 1 }} />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    expect(post).not.toHaveBeenCalled();

    send({ type: 'ready' });
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 } }, '*');

    rerender(<WidgetFrame srcDoc="<p></p>" title="w" state={{ n: 2 }} />);
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 2 } }, '*');
  });

  it('passes on events from its own frame', () => {
    const onEvent = vi.fn();
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} onEvent={onEvent} />);
    const { send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    send({ type: 'rendered', atoms: 3 });
    expect(onEvent).toHaveBeenCalledWith({ type: 'rendered', atoms: 3 });
  });

  it('ignores a message from any other window', () => {
    const onEvent = vi.fn();
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} onEvent={onEvent} />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    send({ type: 'ready' }, window);
    send({ type: 'rendered' }, null);
    expect(onEvent).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it.each([[null], ['text'], [{ type: 7 }], [{}]])('ignores the malformed message %j', data => {
    const onEvent = vi.fn();
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} onEvent={onEvent} />);
    stubWidgetFrame(screen.getByTestId('widget-frame')).send(data);
    expect(onEvent).not.toHaveBeenCalled();
  });
});
