// Not every doi field is a real DOI, and only a real one resolves. Accepts a
// bare DOI or one with a doi.org/ or doi: prefix; returns null otherwise.
export function doiUrl(doi: unknown): string | null {
  if (typeof doi !== 'string') return null;
  const bare = doi.trim().replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/i, '');
  if (!/^10\.\d+\//.test(bare)) return null;
  return `https://doi.org/${encodeURIComponent(bare).replace(/%2F/g, '/')}`;
}
