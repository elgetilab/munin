/**
 * Munin Support Widget
 * Floating support button + contact form popup.
 * Include on any page: <script src="/shared/support-widget.js"></script>
 */
(function () {
  const AUTH_URL = '{{env "MUNIN_URL_AUTH"}}';
  const SUPPORT_EMAIL = '{{env "MUNIN_SUPPORT_EMAIL"}}';

  // Don't double-init
  if (document.getElementById('munin-support-btn')) return;

  // ── Styles ──────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #munin-support-btn {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 9999;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: var(--bg-secondary, #1a1f26);
      border: 1px solid var(--border, #30363d);
      color: var(--text-secondary, #8b949e);
      font-size: 20px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s, color 0.2s, box-shadow 0.2s;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
    #munin-support-btn:hover {
      background: var(--bg-tertiary, #242a33);
      color: var(--accent, #58a6ff);
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }

    #munin-support-overlay {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    #munin-support-backdrop {
      position: absolute;
      inset: 0;
      background: rgba(15, 20, 25, 0.7);
      backdrop-filter: blur(4px);
    }

    /* Light pages. The colours above are theme variables, which style.css and the
       chat app's index.css both define for light and dark. These rules only redo
       what the dark look got from fixed values, so dark stays as it was. */
    :root[data-theme="light"] #munin-support-btn {
      /* The page and the button are both near white: 4.55:1 for the edge */
      border-color: var(--border-input);
      box-shadow: 0 2px 8px rgba(31, 36, 41, 0.15);
    }
    :root[data-theme="light"] #munin-support-btn:hover {
      box-shadow: 0 4px 16px rgba(31, 36, 41, 0.2);
    }
    :root[data-theme="light"] #munin-support-backdrop {
      background: var(--scrim);
    }
    :root[data-theme="light"] #munin-support-panel {
      box-shadow: 0 8px 32px rgba(31, 36, 41, 0.2);
    }
    /* Faded secondary text would be 2.65:1 on white; tertiary is 5.25:1 */
    :root[data-theme="light"] #munin-support-panel input::placeholder,
    :root[data-theme="light"] #munin-support-panel textarea::placeholder {
      color: var(--text-tertiary);
      opacity: 1;
    }
    #munin-support-panel {
      position: relative;
      width: 100%;
      max-width: 440px;
      background: var(--bg-secondary, #1a1f26);
      border: 1px solid var(--border, #30363d);
      border-radius: 12px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    }
    #munin-support-panel .sp-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border, #30363d);
    }
    #munin-support-panel .sp-header h3 {
      margin: 0;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-primary, #e6edf3);
      flex: 1;
    }
    #munin-support-panel .sp-close {
      background: none;
      border: none;
      color: var(--text-secondary, #8b949e);
      cursor: pointer;
      font-size: 14px;
      padding: 4px;
    }
    #munin-support-panel .sp-close:hover {
      color: var(--text-primary, #e6edf3);
    }
    #munin-support-panel .sp-body {
      padding: 20px;
    }
    #munin-support-panel .sp-email-info {
      font-size: 12px;
      color: var(--text-secondary, #8b949e);
      margin-bottom: 16px;
      line-height: 1.5;
    }
    #munin-support-panel .sp-email-info a {
      color: var(--accent, #58a6ff);
      text-decoration: none;
    }
    #munin-support-panel .sp-email-info a:hover {
      text-decoration: underline;
    }
    #munin-support-panel label {
      display: block;
      font-size: 12px;
      color: var(--text-secondary, #8b949e);
      margin-bottom: 4px;
    }
    #munin-support-panel input,
    #munin-support-panel textarea {
      width: 100%;
      background: var(--bg-primary, #0f1419);
      border: 1px solid var(--border-input, #6e7681);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--text-primary, #e6edf3);
      box-sizing: border-box;
      font-family: inherit;
    }
    #munin-support-panel input:focus:not(:focus-visible),
    #munin-support-panel textarea:focus:not(:focus-visible) {
      border-color: var(--accent, #58a6ff);
    }
    /* Repeated from style.css because the chat app does not load it */
    #munin-support-btn:focus-visible,
    #munin-support-panel a:focus-visible,
    #munin-support-panel button:focus-visible,
    #munin-support-panel input:focus-visible,
    #munin-support-panel textarea:focus-visible {
      outline: 2px solid var(--focus-ring, var(--accent, #58a6ff));
      outline-offset: 2px;
    }
    #munin-support-panel input::placeholder,
    #munin-support-panel textarea::placeholder {
      color: var(--text-secondary, #8b949e);
      opacity: 0.6;
    }
    #munin-support-panel textarea {
      resize: none;
      min-height: 120px;
    }
    #munin-support-panel .sp-field {
      margin-bottom: 12px;
    }
    #munin-support-panel .sp-footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      padding: 12px 20px;
      border-top: 1px solid var(--border, #30363d);
    }
    #munin-support-panel .sp-btn {
      padding: 6px 16px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      border: none;
      font-family: inherit;
      text-decoration: none;
      transition: background 0.15s;
    }
    #munin-support-panel .sp-btn-cancel {
      background: none;
      color: var(--text-secondary, #8b949e);
    }
    #munin-support-panel .sp-btn-cancel:hover {
      color: var(--text-primary, #e6edf3);
    }
    #munin-support-panel .sp-btn-send {
      background: var(--accent, #58a6ff);
      color: var(--bg-primary, #0f1419);
    }
    #munin-support-panel .sp-btn-send:hover {
      background: var(--accent-hover, #79b8ff);
    }
    #munin-support-panel .sp-btn-send:disabled {
      opacity: 0.5;
      cursor: default;
    }
    #munin-support-panel .sp-error {
      font-size: 12px;
      color: var(--error, #f85149);
      margin-top: 4px;
    }
    #munin-support-panel .sp-success {
      text-align: center;
      padding: 32px 20px;
      color: var(--text-primary, #e6edf3);
    }
    #munin-support-panel .sp-success p {
      font-size: 14px;
      margin: 8px 0 0;
      color: var(--text-secondary, #8b949e);
    }

    @media (max-width: 480px) {
      #munin-support-btn {
        bottom: 16px;
        right: 16px;
      }
      /* Room under the page footer, so the button does not cover its text */
      body > footer {
        padding-bottom: 76px;
      }
      #munin-support-panel .sp-close {
        min-width: 44px;
        min-height: 44px;
        margin: -12px -12px -12px 0;
        font-size: 20px;
      }
      #munin-support-panel .sp-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 44px;
        font-size: 14px;
      }
      /* Under 16px, iOS Safari zooms the page when a field gets focus */
      #munin-support-panel input,
      #munin-support-panel textarea {
        font-size: 16px;
      }
      #munin-support-panel {
        max-width: none;
        border-radius: 12px 12px 0 0;
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
      }
      #munin-support-overlay {
        align-items: flex-end;
        padding: 0;
      }
    }
  `;
  document.head.appendChild(style);

  // ── Floating button ─────────────────────────────────────────────────
  const btn = document.createElement('button');
  btn.id = 'munin-support-btn';
  btn.title = 'Contact support';
  btn.setAttribute('aria-label', 'Contact support');
  btn.innerHTML = '?';
  btn.addEventListener('click', openSupport);
  document.body.appendChild(btn);

  window.MuninSupport = { open: openSupport };

  // What had focus when the panel opened, for closeSupport to return to.
  let opener = null;

  // Keeps Tab inside the panel, whose contents change as the form is answered.
  function trapTab(e, panel) {
    const items = [...panel.querySelectorAll('a[href], button, input, textarea')].filter((el) => !el.disabled);
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!panel.contains(active) || active === panel) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // ── Support panel ───────────────────────────────────────────────────
  function openSupport() {
    if (document.getElementById('munin-support-overlay')) return;

    opener = document.activeElement;

    const overlay = document.createElement('div');
    overlay.id = 'munin-support-overlay';

    const backdrop = document.createElement('div');
    backdrop.id = 'munin-support-backdrop';
    backdrop.addEventListener('click', closeSupport);
    overlay.appendChild(backdrop);

    const panel = document.createElement('div');
    panel.id = 'munin-support-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', 'munin-sp-title');
    panel.tabIndex = -1;
    panel.innerHTML = `
      <div class="sp-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent, #58a6ff); flex-shrink: 0;">
          <circle cx="12" cy="12" r="10"/>
          <path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01"/>
        </svg>
        <h3 id="munin-sp-title">Contact Support</h3>
        <button type="button" class="sp-close" aria-label="Close support" data-sp-close>&times;</button>
      </div>
      <div class="sp-body">
        <div class="sp-email-info">
          You can also email us directly at <a href="mailto:${SUPPORT_EMAIL}">${SUPPORT_EMAIL}</a>
        </div>
        <div class="sp-field">
          <label for="munin-sp-subject">Subject (optional)</label>
          <input type="text" id="munin-sp-subject" placeholder="Brief description of the issue" maxlength="200">
        </div>
        <div class="sp-field">
          <label for="munin-sp-message">Message</label>
          <textarea id="munin-sp-message" placeholder="Describe what happened, what you expected, and which page you were on..."></textarea>
        </div>
        <div id="munin-sp-error" class="sp-error" role="alert" style="display:none"></div>
      </div>
      <div class="sp-footer">
        <button type="button" class="sp-btn sp-btn-cancel" data-sp-close>Cancel</button>
        <button type="button" class="sp-btn sp-btn-send" id="munin-sp-send">Send message</button>
      </div>
    `;
    // The footer is replaced as the form is answered, so listen on the panel.
    panel.addEventListener('click', (e) => {
      if (e.target.closest('[data-sp-close]')) closeSupport();
    });
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    // Focus message field
    setTimeout(() => document.getElementById('munin-sp-message')?.focus(), 50);

    // Escape closes, and Tab stays inside. On the document, so that both still
    // work when focus has fallen out of the panel. This panel sits above anything
    // the page has open, so these two keys are its alone: in the capture phase and
    // stopped there, or a dialog underneath would close too, or pull Tab away.
    const keyHandler = (e) => {
      if (e.key !== 'Escape' && e.key !== 'Tab') return;
      e.stopImmediatePropagation();
      if (e.key === 'Escape') closeSupport();
      else trapTab(e, panel);
    };
    document.addEventListener('keydown', keyHandler, true);
    overlay._keyHandler = keyHandler;

    // Send button
    document.getElementById('munin-sp-send').addEventListener('click', submitSupport);

    showEmailIfSignedOut(panel);
  }

  // /support/contact needs a session, so a visitor without one gets the email
  // address before typing a message that can't be sent. Only a 401 means that:
  // on any other failure, CORS and network errors included, the form stays.
  async function showEmailIfSignedOut(panel) {
    let res;
    try {
      res = await fetch(AUTH_URL + '/auth/me', { credentials: 'include' });
    } catch (e) {
      return;
    }
    if (res.status !== 401 || !panel.isConnected) return;
    // On a slow check the visitor may have started typing: keep their text. The
    // form already names the address, and Send reports the missing session.
    if (panel.querySelector('#munin-sp-subject')?.value || panel.querySelector('#munin-sp-message')?.value) return;

    panel.querySelector('.sp-body').innerHTML = `
      <div class="sp-email-info" style="margin-bottom: 0;">
        Sign in to send a message here, or email us at <a href="mailto:${SUPPORT_EMAIL}">${SUPPORT_EMAIL}</a>
      </div>
    `;
    panel.querySelector('.sp-footer').innerHTML = `
      <button type="button" class="sp-btn sp-btn-cancel" data-sp-close>Close</button>
      <a class="sp-btn sp-btn-send" href="${AUTH_URL}/login?redirect=${encodeURIComponent(location.href)}">Sign in</a>
    `;
    panel.querySelector('.sp-btn-send').focus();
  }

  function closeSupport() {
    const overlay = document.getElementById('munin-support-overlay');
    if (overlay) {
      if (overlay._keyHandler) document.removeEventListener('keydown', overlay._keyHandler, true);
      overlay.remove();
      // The opener may be gone, or may have been nothing in particular.
      const target = opener && opener !== document.body && opener.isConnected ? opener : btn;
      opener = null;
      target.focus();
    }
  }

  async function submitSupport() {
    const subject = document.getElementById('munin-sp-subject')?.value || '';
    const message = document.getElementById('munin-sp-message')?.value || '';
    const errorEl = document.getElementById('munin-sp-error');
    const sendBtn = document.getElementById('munin-sp-send');

    if (!message.trim()) {
      errorEl.textContent = 'Please describe the issue.';
      errorEl.style.display = 'block';
      return;
    }

    errorEl.style.display = 'none';
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending...';

    try {
      const res = await fetch(AUTH_URL + '/support/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ subject: subject.trim(), message: message.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Failed to send message');
      }

      // Show success state
      const panel = document.getElementById('munin-support-panel');
      if (panel) {
        panel.querySelector('.sp-body').innerHTML = `
          <div class="sp-success">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--success, #3fb950)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 11.08V12a10 10 0 11-5.93-9.14"/>
              <polyline points="22 4 12 14.01 9 11.01"/>
            </svg>
            <p>Message sent. We'll get back to you soon.</p>
          </div>
        `;
        panel.querySelector('.sp-footer').innerHTML = `
          <button type="button" class="sp-btn sp-btn-cancel" data-sp-close>Close</button>
        `;
        // The button that had focus has just gone.
        panel.querySelector('[data-sp-close].sp-btn').focus();
      }
    } catch (e) {
      errorEl.textContent = e.message;
      errorEl.style.display = 'block';
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send message';
      sendBtn.focus();
    }
  }
})();
