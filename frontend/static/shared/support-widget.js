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
      border: 1px solid var(--border, #30363d);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--text-primary, #e6edf3);
      outline: none;
      box-sizing: border-box;
      font-family: inherit;
    }
    #munin-support-panel input:focus,
    #munin-support-panel textarea:focus {
      border-color: var(--accent, #58a6ff);
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
        width: 40px;
        height: 40px;
        font-size: 18px;
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
  btn.innerHTML = '?';
  btn.addEventListener('click', openSupport);
  document.body.appendChild(btn);

  // ── Support panel ───────────────────────────────────────────────────
  function openSupport() {
    if (document.getElementById('munin-support-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'munin-support-overlay';

    const backdrop = document.createElement('div');
    backdrop.id = 'munin-support-backdrop';
    backdrop.addEventListener('click', closeSupport);
    overlay.appendChild(backdrop);

    const panel = document.createElement('div');
    panel.id = 'munin-support-panel';
    panel.innerHTML = `
      <div class="sp-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent, #58a6ff); flex-shrink: 0;">
          <circle cx="12" cy="12" r="10"/>
          <path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01"/>
        </svg>
        <h3>Contact Support</h3>
        <button class="sp-close" onclick="document.getElementById('munin-support-overlay')?.remove()">&times;</button>
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
        <div id="munin-sp-error" class="sp-error" style="display:none"></div>
      </div>
      <div class="sp-footer">
        <button class="sp-btn sp-btn-cancel" onclick="document.getElementById('munin-support-overlay')?.remove()">Cancel</button>
        <button class="sp-btn sp-btn-send" id="munin-sp-send">Send message</button>
      </div>
    `;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    // Focus message field
    setTimeout(() => document.getElementById('munin-sp-message')?.focus(), 50);

    // Escape to close
    const escHandler = (e) => {
      if (e.key === 'Escape') closeSupport();
    };
    document.addEventListener('keydown', escHandler);
    overlay._escHandler = escHandler;

    // Send button
    document.getElementById('munin-sp-send').addEventListener('click', submitSupport);
  }

  function closeSupport() {
    const overlay = document.getElementById('munin-support-overlay');
    if (overlay) {
      if (overlay._escHandler) document.removeEventListener('keydown', overlay._escHandler);
      overlay.remove();
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
          <button class="sp-btn sp-btn-cancel" onclick="document.getElementById('munin-support-overlay')?.remove()">Close</button>
        `;
      }
    } catch (e) {
      errorEl.textContent = e.message;
      errorEl.style.display = 'block';
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send message';
    }
  }
})();
