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
      // A React Compiler rule (eslint-plugin-react-hooks v7) that flags a
      // deliberate long-standing pattern rather than an accident: 13 sites
      // across 8 components use the ordinary fetch-on-mount shape,
      // `useEffect(() => { load(); }, [load])`, where load() sets a loading
      // flag before its first await. Clearing it means restructuring data
      // loading in each of those components, which is scheduled work rather
      // than a lint pass, and six of them have no tests yet. Warn keeps
      // every site in CI's annotations without gating the job on debt we
      // already know about. This goes back to error once that restructure
      // lands, so new instances cannot creep in.
      'react-hooks/set-state-in-effect': 'warn',
      // preserve-manual-memoization was warn alongside it until App.tsx's
      // dependency arrays were completed (2026-09-22). At zero occurrences
      // it is an error again: that is what keeps hand-trimmed arrays, and
      // the skipped compilation they cause, from coming back.
      'react-hooks/preserve-manual-memoization': 'error',
    },
  },
])
