import { doiUrl } from './doi';

describe('doiUrl', () => {
  it('links bare and prefixed DOIs and rejects others', () => {
    expect(doiUrl('10.1000/xyz')).toBe('https://doi.org/10.1000/xyz');
    expect(doiUrl(' https://doi.org/10.1000/a b ')).toBe('https://doi.org/10.1000/a%20b');
    expect(doiUrl('doi: 10.1000/x')).toBe('https://doi.org/10.1000/x');
    expect(doiUrl('not-a-doi')).toBeNull();
    expect(doiUrl(undefined)).toBeNull();
    expect(doiUrl(42)).toBeNull();
  });
});
