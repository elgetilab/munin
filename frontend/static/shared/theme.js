/*
 * Theme toggle for the static pages: one sun/moon button that flips light and dark.
 *
 * Same rules as webui/src/lib/theme.ts, which the chat app uses: the choice is the
 * munin_theme cookie on the domain the Munin sites share. Without one the page
 * follows the system ("system"), and the first click stores light or dark; Match
 * system can be chosen again in the chat's Settings. The inline head script
 * (shared/theme-snippet.html) has already set data-theme before any CSS loaded.
 * This adds the button to the page header and keeps the page in step when the
 * choice changes in another tab, on another Munin site, or in the system settings.
 *
 * After a change it fires "munin-themechange" on document, with detail
 * { pref, theme }, for page code that cannot follow CSS variables (Uppy).
 *
 * Caddy serves this file through its templates, so it must never contain two
 * opening curly braces in a row.
 */
(function () {
    'use strict';

    var COOKIE = 'munin_theme';
    var ONE_YEAR = 60 * 60 * 24 * 365;
    // The subdomains Munin serves. A choice made on any of them must reach the
    // others, so the cookie goes on the domain they share.
    var SITE_SUBDOMAINS = ['chat', 'auth', 'docs', 'search', 'research', 'upload'];
    var root = document.documentElement;
    var buttons = [];

    /** The Domain attribute for the cookie, or null for a host-only cookie. */
    function cookieDomain(hostname) {
        var h = String(hostname).toLowerCase();
        // localhost and IP addresses cannot take a Domain attribute. Cookies
        // ignore ports, so the local stack's separate ports still share one.
        if (h === 'localhost' || /^[\d.]+$/.test(h) || h.indexOf(':') !== -1) return null;
        var parts = h.split('.');
        var first = parts[0];
        var rest = parts.slice(1);
        if (SITE_SUBDOMAINS.indexOf(first) !== -1 && rest.length >= 2) return rest.join('.');
        return rest.length >= 1 ? h : null;
    }

    /** The preference stored in a cookie string. */
    function readPref(cookie) {
        var m = String(cookie).match(/(?:^|;\s*)munin_theme=(light|dark|system)(?:;|$)/);
        return m ? m[1] : 'system';
    }

    function systemTheme() {
        try {
            return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        } catch (e) {
            return 'dark';
        }
    }

    function resolveTheme(pref) {
        return pref === 'system' ? systemTheme() : pref;
    }

    function currentPref() {
        try {
            return readPref(document.cookie);
        } catch (e) {
            return 'system';
        }
    }

    /** Sets data-theme on <html> from the stored choice, as the head script does. */
    function apply(pref) {
        if (pref === undefined) pref = currentPref();
        var theme = resolveTheme(pref);
        var changed = root.getAttribute('data-theme') !== theme ||
            root.getAttribute('data-theme-pref') !== pref;
        root.setAttribute('data-theme-pref', pref);
        root.setAttribute('data-theme', theme);
        for (var i = 0; i < buttons.length; i++) label(buttons[i], theme);
        // The browser's own UI follows the page, where the page asks for that.
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            var bg = getComputedStyle(root).getPropertyValue('--bg-primary').trim();
            if (bg) meta.setAttribute('content', bg);
        }
        if (changed) {
            document.dispatchEvent(new CustomEvent('munin-themechange', { detail: { pref: pref, theme: theme } }));
        }
        return theme;
    }

    function setPref(pref) {
        var domain = cookieDomain(window.location.hostname);
        var secure = window.location.protocol === 'https:' ? '; Secure' : '';
        document.cookie = COOKIE + '=' + pref + '; Path=/; Max-Age=' + ONE_YEAR + '; SameSite=Lax' +
            (domain ? '; Domain=' + domain : '') + secure;
        return apply(pref);
    }

    var SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
    // The button shows where a click goes: a sun on a dark page, a moon on a light one.
    var SUN = SVG + '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41' +
        'M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';
    var MOON = SVG + '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/></svg>';

    /** Draws the button for the theme now showing, naming what a click does. */
    function label(button, theme) {
        var next = theme === 'dark' ? 'light' : 'dark';
        var text = 'Switch to ' + next + ' theme';
        button.innerHTML = theme === 'dark' ? SUN : MOON;
        button.setAttribute('aria-label', text);
        button.title = text;
    }

    /** Adds the toggle to the page header, after the nav. */
    function mount() {
        var host = document.querySelector('header .header-content');
        if (!host || host.querySelector('.theme-toggle')) return;
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'theme-toggle';
        label(button, resolveTheme(currentPref()));
        button.addEventListener('click', function () {
            setPref(root.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
        });
        host.appendChild(button);
        host.classList.add('has-theme-toggle');
        buttons.push(button);
    }

    // A cookie change fires no event, so another tab or site's choice is picked
    // up when this tab comes back into view.
    window.addEventListener('focus', function () { apply(); });
    // A page restored from the back-forward cache keeps the theme it was left with.
    window.addEventListener('pageshow', function () { apply(); });
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') apply();
    });
    try {
        var query = window.matchMedia('(prefers-color-scheme: light)');
        var onSystemChange = function () { apply(); };
        if (query.addEventListener) query.addEventListener('change', onSystemChange);
        else if (query.addListener) query.addListener(onSystemChange);
    } catch (e) {
        /* no matchMedia: the system choice is read again on focus */
    }

    window.MuninTheme = {
        cookieDomain: cookieDomain,
        readPref: readPref,
        resolveTheme: resolveTheme,
        getPref: currentPref,
        setPref: setPref,
        apply: apply,
        mount: mount
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { mount(); apply(); });
    } else {
        mount();
        apply();
    }
})();
