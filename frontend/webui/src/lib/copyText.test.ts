import { copyText } from './copyText';

function stubClipboard(clipboard: unknown) {
  vi.stubGlobal('navigator', { ...navigator, clipboard });
}

describe('copyText', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('writes the text and resolves true', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard({ writeText });
    await expect(copyText('hello')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
  });

  it('resolves false when permission is refused', async () => {
    stubClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')) });
    await expect(copyText('hello')).resolves.toBe(false);
  });

  it('resolves false when the Clipboard API does not exist', async () => {
    stubClipboard(undefined);
    await expect(copyText('hello')).resolves.toBe(false);
  });

  it('waits for the write before answering', async () => {
    let finish = () => {};
    stubClipboard({ writeText: () => new Promise<void>(resolve => { finish = resolve; }) });
    let answer: boolean | undefined;
    const pending = copyText('hello').then(result => { answer = result; });
    await Promise.resolve();
    expect(answer).toBeUndefined();
    finish();
    await pending;
    expect(answer).toBe(true);
  });
});
