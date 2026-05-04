/**
 * Time-of-day aware greeting messages for the welcome screen.
 * Uses the user's first name for a personal touch.
 */

interface GreetingSet {
  range: [number, number]; // [startHour, endHour) in 24h format
  messages: string[];
}

const GREETING_SETS: GreetingSet[] = [
  {
    range: [5, 12], // Early morning to noon
    messages: [
      'Good morning, {name}',
      'Morning, {name}',
      'Rise and shine, {name}',
      'The ravens are awake, {name}',
      'The scrolls await, {name}',
      'Fresh coffee, fresh questions, {name}?',
      'What knowledge do we seek today, {name}?',
    ],
  },
  {
    range: [12, 17], // Noon to evening
    messages: [
      'Good afternoon, {name}',
      'The ravens circle, {name}',
      'Welcome back, {name}',
      'Hey {name}',
      'The sun is high, {name}',
      'Pondering the orb, {name}?',
      'Where were we, {name}?',
    ],
  },
  {
    range: [17, 22], // Evening
    messages: [
      'Good evening, {name}',
      'The ravens return, {name}',
      'Still chasing knowledge, {name}?',
      'One more question before nightfall, {name}?',
      'Winding down or just warming up, {name}?',
      'Curiosity never sleeps, {name}',
    ],
  },
  {
    range: [22, 5], // Late night (wraps around midnight)
    messages: [
      'Burning the midnight oil, {name}?',
      'The stars are out, {name}',
      'Night owl mode, {name}',
      'The quiet hours, {name}',
      'Can\'t sleep, {name}?',
      'The best ideas come after dark, {name}',
    ],
  },
];

// Greetings that work at any time of day, with or without a name
const ANYTIME_MESSAGES: string[] = [
  'Back at it, {name}',
  'Back at it!',
  'What shall we explore?',
  'The ravens await',
  'Ready when you are',
  'Ask away',
  'At your service',
  'Let\'s get to it',
];

function getFirstName(fullName: string): string {
  return fullName.split(/\s+/)[0];
}

function getGreetingSet(hour: number): GreetingSet {
  // Late night wraps around midnight: 22-5
  for (const set of GREETING_SETS) {
    const [start, end] = set.range;
    if (start > end) {
      // Wrapping range (e.g., 22–5)
      if (hour >= start || hour < end) return set;
    } else {
      if (hour >= start && hour < end) return set;
    }
  }
  return GREETING_SETS[0]; // fallback to morning
}

export function getGreeting(name: string | null): string {
  const hour = new Date().getHours();
  const set = getGreetingSet(hour);
  // ~30% chance to pick from anytime pool
  const pool = Math.random() < 0.3 ? ANYTIME_MESSAGES : set.messages;
  const message = pool[Math.floor(Math.random() * pool.length)];
  if (name) {
    return message.replace('{name}', getFirstName(name));
  }
  // No name: remove ", {name}" or " {name}" or "{name}" cleanly
  return message.replace(/,?\s*\{name\}/g, '').replace(/\s+([?!])/g, '$1').trim();
}
