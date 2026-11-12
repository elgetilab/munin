import {
  artifactRefsFromToolCalls,
  fileSizeBytes,
  isEmbeddedInText,
  isFilePlaceholder,
  isInlinePreview,
  isWidget,
  parseProteinSpec,
  WIDGET_CONTENT_TYPE,
  MAX_INLINE_PREVIEW_BYTES,
  svgDataUrl,
  type ArtifactRef,
} from './artifactDisplay';
import type { ToolCall } from './types';

const plot = {
  id: 'sandbox-hash',
  filename: 'plot.png',
  content_type: 'image/png',
  size_bytes: 2048,
  registered_artifact_id: 'art_plot',
  external_url: '/api/artifacts/c1/sandbox-hash',
};

function call(name: string, result: unknown): ToolCall {
  return { name, arguments: {}, result };
}

function ref(overrides: Partial<ArtifactRef> = {}): ArtifactRef {
  return { id: 'a', title: 't', content_type: 'image/png', source: 'sandbox_generated', external_url: '/api/artifacts/c1/a', ...overrides };
}

describe('sandbox file bodies', () => {
  const placeholder = '[Sandbox-generated file: a.bin (application/octet-stream, 2048 bytes). The actual bytes live in the sandbox container; fetch them via GET /api/artifacts/c/h.]';

  it('recognises the stored placeholder and an empty sandbox body', () => {
    expect(isFilePlaceholder({ source: 'sandbox_generated', content: placeholder })).toBe(true);
    expect(isFilePlaceholder({ source: 'sandbox_generated', content: '  ' })).toBe(true);
  });

  it('leaves real text alone, including text a sandbox file stores', () => {
    expect(isFilePlaceholder({ source: 'sandbox_generated', content: '\\documentclass{article}' })).toBe(false);
    expect(isFilePlaceholder({ source: 'model_written', content: '' })).toBe(false);
  });

  it('reads the size from the record or the placeholder', () => {
    expect(fileSizeBytes({ size_bytes: 10, content: placeholder })).toBe(10);
    expect(fileSizeBytes({ content: placeholder })).toBe(2048);
    expect(fileSizeBytes({ content: 'a paper, 2048 bytes)' })).toBeNull();
  });
});

describe('artifactRefsFromToolCalls', () => {
  it('returns nothing without tool calls', () => {
    expect(artifactRefsFromToolCalls(null)).toEqual([]);
    expect(artifactRefsFromToolCalls(undefined)).toEqual([]);
    expect(artifactRefsFromToolCalls([])).toEqual([]);
  });

  it.each(['run_python', 'edit_python', 'compile_latex', 'compute'])('reads sandbox files from %s', name => {
    expect(artifactRefsFromToolCalls([call(name, { artifacts: [plot] })])).toEqual([{
      id: 'art_plot',
      title: 'plot.png',
      content_type: 'image/png',
      source: 'sandbox_generated',
      size_bytes: 2048,
      external_url: '/api/artifacts/c1/sandbox-hash',
    }]);
  });

  it('skips a sandbox file whose registration failed', () => {
    const { registered_artifact_id: _dropped, ...unregistered } = plot;
    expect(artifactRefsFromToolCalls([call('run_python', { artifacts: [unregistered] })])).toEqual([]);
  });

  it('does not treat a list_artifacts result as new artifacts', () => {
    const result = { artifacts: [{ id: 'art_old', title: 'Old', content_type: 'text/markdown' }], total: 1 };
    expect(artifactRefsFromToolCalls([call('list_artifacts', result)])).toEqual([]);
  });

  it('reads a model-written document', () => {
    const result = { id: 'art_doc', title: 'Draft', content_type: 'text/markdown', version: 1 };
    expect(artifactRefsFromToolCalls([call('create_artifact', result)])).toEqual([{
      id: 'art_doc', title: 'Draft', content_type: 'text/markdown', source: 'model_written', version: 1,
    }]);
  });

  it('ignores a failed create or update', () => {
    expect(artifactRefsFromToolCalls([
      call('create_artifact', { error: 'too big' }),
      call('update_artifact', { id: 'art_doc', error: 'no such hunk' }),
    ])).toEqual([]);
  });

  it('ignores a call that has no result yet', () => {
    expect(artifactRefsFromToolCalls([{ name: 'run_python', arguments: {} }])).toEqual([]);
  });

  it('keeps one ref per artifact, at its newest version and first position', () => {
    const refs = artifactRefsFromToolCalls([
      call('create_artifact', { id: 'art_doc', title: 'Draft', content_type: 'text/markdown', version: 1 }),
      call('run_python', { artifacts: [plot] }),
      call('update_artifact', { id: 'art_doc', title: 'Draft', content_type: 'text/markdown', version: 3 }),
    ]);
    expect(refs.map(r => [r.id, r.version])).toEqual([['art_doc', 3], ['art_plot', undefined]]);
  });
});

describe('isInlinePreview', () => {
  it('previews a plot', () => {
    expect(isInlinePreview(ref({ size_bytes: 1024 }))).toBe(true);
  });

  it('previews an image of unknown size', () => {
    expect(isInlinePreview(ref())).toBe(true);
  });

  it('gives an oversized image a card', () => {
    expect(isInlinePreview(ref({ size_bytes: MAX_INLINE_PREVIEW_BYTES + 1 }))).toBe(false);
  });

  it('gives a PDF a card', () => {
    expect(isInlinePreview(ref({ content_type: 'application/pdf', size_bytes: 10 }))).toBe(false);
  });

  it('gives a document a card', () => {
    expect(isInlinePreview(ref({ content_type: 'text/markdown', external_url: undefined, source: 'model_written' }))).toBe(false);
  });

  it('previews a model-written SVG, which has no URL', () => {
    expect(isInlinePreview(ref({ content_type: 'image/svg+xml', external_url: undefined, source: 'model_written' }))).toBe(true);
  });

  it('gives a binary image with no URL a card', () => {
    expect(isInlinePreview(ref({ external_url: undefined }))).toBe(false);
  });
});

describe('isEmbeddedInText', () => {
  it('sees an image the model placed in its text', () => {
    expect(isEmbeddedInText(ref(), 'Here: ![plot](/api/artifacts/c1/a)')).toBe(true);
  });

  it('does not count another artifact, or none', () => {
    expect(isEmbeddedInText(ref(), '![plot](/api/artifacts/c1/other)')).toBe(false);
    expect(isEmbeddedInText(ref(), 'no images here')).toBe(false);
  });
});

describe('svgDataUrl', () => {
  it('encodes the source so it cannot break out of the URL', () => {
    const url = svgDataUrl('<svg xmlns="http://www.w3.org/2000/svg"><text>a#b "c"</text></svg>');
    expect(url.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
    expect(url).not.toMatch(/[<>"#]/);
  });
});

describe('widgets', () => {
  const spec = { widget: 'protein', source: 'pdb', id: '1CRN', style: 'stick', color: 'chain', highlight: [{ chain: 'A', residues: [3] }] };

  it('reads a viewer from show_protein', () => {
    const result = { id: 'art_p', title: 'Crambin', content_type: WIDGET_CONTENT_TYPE, version: 1 };
    const [found] = artifactRefsFromToolCalls([call('show_protein', result)]);
    expect(found.id).toBe('art_p');
    expect(isWidget(found)).toBe(true);
    expect(isInlinePreview(found)).toBe(false);
  });

  it('ignores a failed show_protein', () => {
    expect(artifactRefsFromToolCalls([call('show_protein', { error: 'no PDB entry 9XXX' })])).toEqual([]);
  });

  it('parses a good spec', () => {
    expect(parseProteinSpec(JSON.stringify(spec))).toEqual(spec);
  });

  it.each([
    ['not JSON', 'nope'],
    ['an array', '[]'],
    ['another widget', '{"widget":"other"}'],
    ['an unknown source', '{"widget":"protein","source":"evil","id":"1CRN"}'],
    ['a missing id', '{"widget":"protein","source":"pdb"}'],
  ])('refuses %s', (_label, content) => {
    expect(parseProteinSpec(content)).toBeNull();
  });

  it('falls back on settings the viewer does not know', () => {
    const parsed = parseProteinSpec(JSON.stringify({ ...spec, style: '<script>', color: 'red', highlight: 'all' }));
    expect(parsed).toMatchObject({ style: 'cartoon', color: 'spectrum', highlight: [] });
  });

  it('keeps only whole residue numbers in a highlight', () => {
    const parsed = parseProteinSpec(JSON.stringify({ ...spec, highlight: [{ residues: [1, 'x', 2.5] }, { residues: [] }, 'y'] }));
    expect(parsed?.highlight).toEqual([{ residues: [1] }]);
  });
});
