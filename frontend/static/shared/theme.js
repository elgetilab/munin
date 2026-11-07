/*
 * Theme switch for the static pages: Dark, Light or Match system.
 *
 * Same rules as webui/src/lib/theme.ts, which the chat app uses: the choice is the
 * munin_theme cookie on the domain the Munin sites share, and dark is the default.
 * The inline head script (shared/theme-snippet.html) has already set data-theme
 * before any CSS loaded. This adds the control to the page header and keeps the
 * page in step when the choice changes in another tab, on another Munin site, or
 * in the system settings.
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
    var OPTIONS = [['dark', 'Dark'], ['light', 'Light'], ['system', 'Match system']];
    var root = document.documentElement;
    var selects = [];

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
        return m ? m[1] : 'dark';
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
            return 'dark';
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
        for (var i = 0; i < selects.length; i++) selects[i].value = pref;
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

    var ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/>' +
        '<path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>';

    /** Adds the switch to the page header, after the nav. */
    function mount() {
        var host = document.querySelector('header .header-content');
        if (!host || host.querySelector('.theme-switch')) return;
        var label = document.createElement('label');
        label.className = 'theme-switch';
        label.innerHTML = ICON;
        var select = document.createElement('select');
        select.setAttribute('aria-label', 'Theme');
        select.title = 'Theme';
        for (var i = 0; i < OPTIONS.length; i++) {
            var option = document.createElement('option');
            option.value = OPTIONS[i][0];
            option.textContent = OPTIONS[i][1];
            select.appendChild(option);
        }
        select.value = currentPref();
        select.addEventListener('change', function () { setPref(select.value); });
        label.appendChild(select);
        host.appendChild(label);
        host.classList.add('has-theme-switch');
        selects.push(select);
    }

    // A cookie change fires no event, so another tab or site's choice is picked
    // up when this tab comes back into view.
    window.addEventListener('focus', function () { apply(); });
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
