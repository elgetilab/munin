/**
 * User identity store (P2 #26 Migration commit 3).
 *
 * Owns the "who is logged in" slice: persona registry, currently
 * selected persona, the user's profile, greeting line, admin
 * flag. None of this changes during normal chat use — it's
 * loaded once at mount and read by many components (Sidebar,
 * Settings, ChatInput, MessageList, etc.).
 *
 * Nothing here persists via Zustand middleware — the userProfile
 * comes from `/api/profile` on every mount (authoritative source),
 * and the persona selection is restored from the URL search param
 * `?persona=X` set by the persona switcher.
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

import type { UserProfile } from '../lib/api';
import type { Persona } from '../lib/types';


interface UserState {
  personas: Persona[];
  selectedPersona: string;  // persona id
  greeting: string;
  userProfile: UserProfile | null;
  isAdmin: boolean;

  setPersonas: (personas: Persona[]) => void;
  setSelectedPersona: (id: string) => void;
  setGreeting: (greeting: string) => void;
  setUserProfile: (profile: UserProfile | null) => void;
  setIsAdmin: (isAdmin: boolean) => void;
}


// Initial selected persona. Honour ?persona=X in the URL on first
// load so deep-links from the persona switcher land on the right
// persona. The hadPersonaParam ref in App.tsx used to gate this
// against later programmatic selection — that behaviour moves to
// the App.tsx mount effect, the store just remembers what was
// last selected.
const _initialPersona = (): string => {
  try {
    if (typeof window === 'undefined') return 'chat';
    const fromUrl = new URLSearchParams(window.location.search).get('persona');
    return fromUrl || 'chat';
  } catch {
    return 'chat';
  }
};


export const useUserStore = create<UserState>()(
  immer((set) => ({
    personas: [],
    selectedPersona: _initialPersona(),
    greeting: '',
    userProfile: null,
    isAdmin: false,

    setPersonas: (personas) => set(state => { state.personas = personas; }),
    setSelectedPersona: (id) => set(state => { state.selectedPersona = id; }),
    setGreeting: (greeting) => set(state => { state.greeting = greeting; }),
    setUserProfile: (profile) => set(state => { state.userProfile = profile; }),
    setIsAdmin: (isAdmin) => set(state => { state.isAdmin = isAdmin; }),
  })),
);


export function _resetUserStoreForTests(): void {
  useUserStore.setState({
    personas: [],
    selectedPersona: 'chat',
    greeting: '',
    userProfile: null,
    isAdmin: false,
  });
}
