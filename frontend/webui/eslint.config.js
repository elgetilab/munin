import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // The codebase marks a deliberately-unused binding with a leading
      // underscore: props destructured to document the component's
      // contract but not read, catch bindings kept for shape. That is a
      // convention the default rule cannot see, so it reported three
      // intentional markers as errors. Teach it the convention rather
      // than deleting the markers.
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      // Both React Compiler rules (eslint-plugin-react-hooks v7) were warn
      // for a while because they flagged deliberate long-standing patterns
      // rather than accidents, and clearing them was a restructure rather
      // than a lint pass. That restructure landed over 2026-09-22/23:
      //
      //   preserve-manual-memoization  17 sites, all App.tsx dependency
      //     arrays that had been hand-trimmed to omit (stable) store
      //     actions.
      //   set-state-in-effect  13 sites across 8 components, the
      //     fetch-on-mount shape `useEffect(() => { load(); }, [load])`.
      //     Each component's read now lives in the effect that owns it,
      //     with cancellation, and callers ask for a re-read instead of
      //     performing one. Five of those components had no tests before
      //     the work started and have them now.
      //
      // Both are back at error, which is the point of having done it: the
      // shapes cannot creep back in unnoticed. Note the rule does not
      // reason about `await`, so moving a setState after one does not
      // satisfy it; the read has to be inside the effect.
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/preserve-manual-memoization': 'error',
    },
  },
])
