import type { Locator, Page } from '@playwright/test';

export type Theme = 'light' | 'dark';

/** The theme the page resolved, from the attribute the head script sets. */
export async function themeOf(page: Page): Promise<Theme> {
  return (await page.locator('html').getAttribute('data-theme')) === 'light' ? 'light' : 'dark';
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
