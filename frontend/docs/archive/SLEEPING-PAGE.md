# Off-Hours Sleeping Page

## Overview

Between 2 AM and 6 AM (cluster local time), vLLM is shut down to free GPU resources for batch experiments. During this window, the chat and deep research pages show a branded "sleeping" page instead of a broken interface.

## Which Pages Are Affected

| Page | Off-hours behavior | Why |
|------|-------------------|-----|
| `muninai.org` | **Normal** | Static HTML, no backend dependency |
| `auth.muninai.org` | **Normal** | Auth service runs 24/7 |
| `chat.muninai.org` | **Sleeping page** | Needs vLLM (GPU 1) |
| `search.muninai.org` | **Normal** | Qdrant is always on, no GPU needed |
| `research.muninai.org` | **Sleeping page** | Needs GPU 0 for MiroThinker |
| `upload.muninai.org` | **Normal** | tusd + hook service, no GPU |
| `docs.muninai.org` | **Normal** | Static files |

## Detection

The frontend does NOT hardcode the schedule. Instead it relies on the `/api/status` endpoint which already reports `vllm.status`:

```json
{
  "vllm": {
    "status": "offline",
    "next_start": "2026-04-12T06:00:00+02:00"
  }
}
```

**For the chat frontend (React):** Poll `/api/status` on load and every 60 seconds. When `vllm.status === "offline"`, replace the chat interface with the sleeping page component. When it comes back to `"running"`, swap back automatically — no page reload needed.

**For the research page (static HTML):** On page load, fetch `/api/status`. If `vllm.status === "offline"`, hide the submission form and show the sleeping overlay. Poll every 60 seconds to auto-recover.

### Backend Addition

The `/api/status` endpoint should include `next_start` when vLLM is offline. The backend knows the schedule from the cron configuration (start at 6 AM):

```python
if vllm_status == "offline":
    # Calculate next 6 AM in cluster local time
    now = datetime.now(cluster_tz)
    next_start = now.replace(hour=6, minute=0, second=0)
    if now.hour >= 6:
        next_start += timedelta(days=1)
    status["vllm"]["next_start"] = next_start.isoformat()
```

## Sleeping Page Design

Uses the shared Munin dark theme (`style.css`). Centered content, calm and informative — not an error page.

### Layout

```
┌─────────────────────────────────────────────────────┐
│  [standard Munin header / nav]                      │
│                                                     │
│                                                     │
│          [feather vortex — slow, 96px]               │
│                                                     │
│           Munin is resting                          │
│                                                     │
│   The cluster GPUs are reserved for experiments     │
│   between 2:00 AM and 6:00 AM.                     │
│                                                     │
│   Chat and Deep Research will be back at 6:00 AM.   │
│                                                     │
│   ┌─────────────────────────────────────────┐       │
│   │  Paper Search is still available  →     │       │
│   └─────────────────────────────────────────┘       │
│                                                     │
│   ┌─────────────────────────────────────────┐       │
│   │  Upload papers  →                       │       │
│   └─────────────────────────────────────────┘       │
│                                                     │
│  [standard footer]                                  │
└─────────────────────────────────────────────────────┘
```

### Details

**Feather vortex:** Use the standard vortex animation but slowed down — multiply all speeds by 0.3. The feathers drift lazily instead of storming. This visually communicates "idle/sleeping" rather than "working." Add a `speedMultiplier` parameter to `createVortex()`:

```javascript
// In feather-vortex.js, update the draw loop:
f.angle += f.speed * 0.016 * speedMultiplier;

// Usage for sleeping page:
createVortex(canvas, 10, 5.5, 0.3); // 0.3 = slow drift
```

**Heading:** "Munin is resting" — simple, on-brand.

**Explanation:** Two short lines explaining why and when it comes back. Show the actual next start time from the API (e.g., "back at 6:00 AM") rather than hardcoding, in case the schedule changes or 24/7 mode is enabled.

**Alternative actions:** Link to Paper Search and Upload — these still work and give the user something useful to do. Styled as subtle cards/buttons, not aggressive CTAs.

**No countdown timer.** A ticking clock watching minutes feels tedious. Just state the time.

### Implementation

**For the chat frontend (React):**

Create a `SleepingPage` component that renders when `vllm.status === "offline"`. The chat app's top-level routing:

```jsx
function ChatApp() {
  const status = useStatus(); // polls /api/status

  if (status?.vllm?.status === "offline") {
    return <SleepingPage nextStart={status.vllm.next_start} />;
  }

  return <ChatInterface />;
}
```

**For the research page (static HTML):**

Add a hidden overlay div that becomes visible when the status check returns offline:

```html
<div id="sleeping-overlay" style="display: none;">
  <!-- sleeping page content -->
</div>

<script>
async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const overlay = document.getElementById('sleeping-overlay');
    const main = document.getElementById('research-main');
    if (data.vllm?.status === 'offline') {
      overlay.style.display = 'flex';
      main.style.display = 'none';
    } else {
      overlay.style.display = 'none';
      main.style.display = 'block';
    }
  } catch (e) {
    // API unreachable — show sleeping page as fallback
  }
}
checkStatus();
setInterval(checkStatus, 60000);
</script>
```

## File Locations

- `static/shared/sleeping.html` — shared sleeping page partial (or inline in each page)
- `static/shared/feather-vortex.js` — update to accept `speedMultiplier` parameter
- `static/research/index.html` — add sleeping overlay
- `frontend/src/components/SleepingPage.tsx` — React component for chat frontend

## feather-vortex.js Update

Add a fourth parameter `speedMultiplier` (default 1.0):

```javascript
function createVortex(canvas, featherCount, sizeMultiplier, speedMultiplier) {
  speedMultiplier = speedMultiplier || 1.0;
  // ... existing setup ...

  function draw() {
    // ... existing code ...
    f.angle += f.speed * 0.016 * speedMultiplier;
    // ... rest of draw loop ...
  }
}
```

This way the same animation serves both purposes: full speed for "thinking" and slow drift for "sleeping."
