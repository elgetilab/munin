import { act, render, screen } from '@testing-library/react';
import { WidgetFrame } from './WidgetFrame';
import { stubWidgetFrame } from '../test/widgetFrame';
import { setThemePref } from '../lib/theme';

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
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 }, theme: 'dark' }, '*');

    rerender(<WidgetFrame srcDoc="<p></p>" title="w" state={{ n: 2 }} />);
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 2 }, theme: 'dark' }, '*');
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

describe('WidgetFrame theme', () => {
  // The cookie outlives a test, and dark is the default every other test expects.
  afterEach(() => act(() => setThemePref('dark')));

  it('sends the app theme with the state once the widget is ready', () => {
    setThemePref('light');
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{ n: 1 }} />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    expect(post).not.toHaveBeenCalled();
    send({ type: 'ready' });
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 }, theme: 'light' }, '*');
  });

  it('sends it again when the theme changes, with the same state', () => {
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{ n: 1 }} />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    send({ type: 'ready' });
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 }, theme: 'dark' }, '*');

    act(() => setThemePref('light'));
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 }, theme: 'light' }, '*');
    act(() => setThemePref('dark'));
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: { n: 1 }, theme: 'dark' }, '*');
    expect(post).toHaveBeenCalledTimes(3);
  });

  it('sends nothing on a theme change before the widget is ready', () => {
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} />);
    const { post } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    act(() => setThemePref('light'));
    expect(post).not.toHaveBeenCalled();
  });

  it('keeps a pinned theme whatever the app uses', () => {
    setThemePref('light');
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} theme="dark" />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    send({ type: 'ready' });
    expect(post).toHaveBeenLastCalledWith({ type: 'state', state: {}, theme: 'dark' }, '*');
    act(() => setThemePref('dark'));
    act(() => setThemePref('light'));
    expect(post).toHaveBeenCalledTimes(1);
  });

  it('still ignores a ready from any other window', () => {
    setThemePref('light');
    render(<WidgetFrame srcDoc="<p></p>" title="w" state={{}} />);
    const { post, send } = stubWidgetFrame(screen.getByTestId('widget-frame'));
    send({ type: 'ready' }, window);
    act(() => setThemePref('dark'));
    expect(post).not.toHaveBeenCalled();
  });
});
