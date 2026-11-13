import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

export type Theme = 'light' | 'dark';

/**
 * The theme a project runs in, from its metadata (playwright.config.ts). A spec
 * that checks colours asserts the page really is in it, so a cookie that stopped
 * applying cannot quietly test dark twice.
 */
export function projectTheme(info: TestInfo): Theme {
  return (info.project.metadata as { theme?: Theme } | undefined)?.theme === 'light' ? 'light' : 'dark';
}

/**
 * A few tokens as src/index.css sets them, as the browser reports them. Pinned
 * on purpose: reading a token back from the page cannot notice the token itself
 * going wrong, so one test per theme holds the values still.
 */
export const PALETTE: Record<Theme, { accent: string; border: string; bgPrimary: string; textPrimary: string }> = {
  dark: {
    accent: 'rgb(217, 181, 111)',
    border: 'rgb(37, 47, 57)',
    bgPrimary: 'rgb(13, 18, 23)',
    textPrimary: 'rgb(230, 237, 243)',
  },
  light: {
    accent: 'rgb(10, 92, 184)',
    border: 'rgb(221, 227, 233)',
    bgPrimary: 'rgb(255, 255, 255)',
    textPrimary: 'rgb(24, 32, 41)',
  },
};

/** The theme the page resolved, from the attribute the head script sets. */
export async function themeOf(page: Page): Promise<Theme> {
  return (await page.locator('html').getAttribute('data-theme')) === 'light' ? 'light' : 'dark';
}

/** Fails unless the page is in the theme this project is meant to run in. */
export async function expectProjectTheme(page: Page, info: TestInfo): Promise<Theme> {
  const theme = projectTheme(info);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  return theme;
}

/**
 * A CSS custom property as the browser computes it where `el` sits, as an rgb()
 * string. A probe is put beside the element, so a value scoped to a subtree (a
 * panel that stays dark in both themes, say) is read as that subtree sees it.
 * Tests compare against this instead of a pinned colour, so they hold in both
 * themes and still fail if the element stops using the token.
 */
export async function cssVarColor(el: Locator, name = '--color-accent'): Promise<string> {
  return el.evaluate((node, varName) => {
    const host = node.parentElement ?? node;
    const probe = document.createElement('span');
    probe.style.color = `var(${varName})`;
    host.appendChild(probe);
    const style = getComputedStyle(probe);
    const defined = style.getPropertyValue(varName).trim() !== '';
    const value = style.color;
    probe.remove();
    // An undefined variable leaves the inherited colour, which a test would then
    // compare against without noticing.
    if (!defined) throw new Error(`${varName} is not defined here`);
    return value;
  }, name);
}
