import { getGreeting } from './greetings';

describe('getGreeting', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0.5); // always pick from time-of-day pool
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('returns a morning greeting between 5-12', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 8, 0, 0));
    const greeting = getGreeting('Alice');
    const morningPatterns = ['morning', 'Rise and shine', 'ravens are awake', 'scrolls await', 'Fresh coffee', 'knowledge'];
    expect(morningPatterns.some((p) => greeting.toLowerCase().includes(p.toLowerCase()))).toBe(true);
  });

  it('returns an afternoon greeting between 12-17', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 14, 0, 0));
    const greeting = getGreeting('Bob');
    const afternoonPatterns = ['afternoon', 'ravens circle', 'Welcome back', 'Hey', 'sun is high', 'Pondering', 'Where were we'];
    expect(afternoonPatterns.some((p) => greeting.includes(p))).toBe(true);
  });

  it('returns an evening greeting between 17-22', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 19, 0, 0));
    const greeting = getGreeting('Carol');
    const eveningPatterns = ['evening', 'ravens return', 'chasing knowledge', 'nightfall', 'Winding down', 'never sleeps'];
    expect(eveningPatterns.some((p) => greeting.toLowerCase().includes(p.toLowerCase()))).toBe(true);
  });

  it('returns a late-night greeting between 22-5', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 23, 0, 0));
    const greeting = getGreeting('Dave');
    const lateNightPatterns = ['midnight oil', 'stars are out', 'Night owl', 'quiet hours', "Can't sleep", 'after dark'];
    expect(lateNightPatterns.some((p) => greeting.includes(p))).toBe(true);
  });

  it('returns a late-night greeting at 2 AM (wrap-around)', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 2, 0, 0));
    const greeting = getGreeting('Eve');
    const lateNightPatterns = ['midnight oil', 'stars are out', 'Night owl', 'quiet hours', "Can't sleep", 'after dark'];
    expect(lateNightPatterns.some((p) => greeting.includes(p))).toBe(true);
  });

  it('replaces {name} with first name when name is provided', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 8, 0, 0));
    const greeting = getGreeting('Alice Wonderland');
    expect(greeting).toContain('Alice');
    expect(greeting).not.toContain('Wonderland');
    expect(greeting).not.toContain('{name}');
  });

  it('strips {name} and cleans punctuation when name is null', () => {
    vi.setSystemTime(new Date(2026, 3, 15, 8, 0, 0));
    const greeting = getGreeting(null);
    expect(greeting).not.toContain('{name}');
    expect(greeting).not.toMatch(/,\s*$/);
    expect(greeting).not.toMatch(/\s{2,}/);
    expect(greeting.length).toBeGreaterThan(0);
  });
});
