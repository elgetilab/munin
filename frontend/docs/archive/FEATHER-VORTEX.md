# Munin Feather Vortex — Thinking Indicator

## Overview

An animated "feather storm" used as the thinking/loading indicator across the Munin platform. Raven feathers caught in a vortex — they orbit at different speeds (overtaking each other), breathe in and out toward the center, and fade with depth. It replaces a generic spinner everywhere the UI needs to show "working."

## Where It's Used

- **Chat UI** — inline next to "Thinking..." while waiting for LLM response (28–40px)
- **Deep Research page** — loading state while job is running (96–160px)
- **Auth login page** — after submitting OTP, while verifying (40px)
- **Any loading state** across the platform

## Implementation

Use HTML Canvas (not SVG animation). The prototype uses `requestAnimationFrame` for smooth 60fps rendering. The component should be a self-contained function that takes a canvas element and optional config.

### Feather Shape

Each feather is drawn with bezier curves — asymmetric, with a curved rachis (spine), a wider vane on one side, and a tapered tip. Not a teardrop or blob.

```javascript
function drawFeather(ctx, x, y, size, angle, opacity) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.globalAlpha = opacity;

  // Feather body — asymmetric vane shape
  ctx.beginPath();
  ctx.moveTo(0, -size * 1.8);                                          // tip
  ctx.bezierCurveTo(size * 0.35, -size * 1.5, size * 0.75, -size * 0.8, size * 0.7, 0);  // right vane out
  ctx.bezierCurveTo(size * 0.65, size * 0.6, size * 0.35, size * 1.2, 0, size * 1.6);    // right vane to base
  ctx.bezierCurveTo(-size * 0.18, size * 1.0, -size * 0.25, size * 0.3, -size * 0.18, -size * 0.2); // left vane
  ctx.bezierCurveTo(-size * 0.12, -size * 0.8, -size * 0.06, -size * 1.4, 0, -size * 1.8);         // back to tip
  ctx.fillStyle = '#e6edf3';
  ctx.fill();

  // Rachis (spine line)
  ctx.beginPath();
  ctx.moveTo(0, -size * 1.7);
  ctx.bezierCurveTo(size * 0.06, -size * 1.0, size * 0.1, -size * 0.2, 0, size * 1.5);
  ctx.strokeStyle = 'rgba(139, 148, 158, 0.5)';
  ctx.lineWidth = Math.max(0.5, size * 0.08);
  ctx.stroke();

  // Barb lines (only render when feather is large enough to see them)
  if (size > 3) {
    ctx.globalAlpha = opacity * 0.3;
    ctx.beginPath();
    var barbs = 5;
    for (var i = 0; i < barbs; i++) {
      var t = (i + 1) / (barbs + 1);
      var spineY = -size * 1.7 + t * size * 3.2;
      var spineX = size * 0.05 * Math.sin(t * Math.PI);
      ctx.moveTo(spineX, spineY);
      ctx.quadraticCurveTo(
        spineX + size * 0.4 * (1 - t * 0.5), spineY + size * 0.15,
        spineX + size * 0.55 * (1 - t * 0.3), spineY + size * 0.3
      );
    }
    ctx.strokeStyle = 'rgba(139, 148, 158, 0.6)';
    ctx.lineWidth = Math.max(0.4, size * 0.05);
    ctx.stroke();
  }

  ctx.restore();
}
```

### Vortex Behavior

Each feather has independent parameters — no two move the same way:

| Parameter | Range | Purpose |
|-----------|-------|---------|
| `speed` | 0.35 – 1.05 | Orbit angular velocity. Different speeds cause overtaking. |
| `baseR` | 0.45 – 0.90 | Base orbit radius (fraction of max). |
| `rFreq` | 0.25 – 0.75 | How fast the radius oscillates (breathing). |
| `rAmp` | 0.10 – 0.28 | How far the radius oscillates. |
| `inwardFreq` | 0.12 – 0.37 | How fast the inward pull cycles. |
| `size` | 0.7x – 1.3x base | Feather size variation. |
| `opBase` | 0.25 – 0.90 | Base opacity (leading feathers brighter). |
| `opFreq` | 0.3 – 0.8 | Opacity pulse speed. |
| `tiltOffset` | -0.35 – +0.35 | Slight angle variation from perfect tangent. |

**Key behaviors:**

1. **Tangent alignment** — feathers point along the orbit (perpendicular to radius), NOT toward the center. They look like they're swept by wind.

2. **Overtaking** — because each feather has a different `speed`, faster ones catch up to and pass slower ones. This is what makes it feel like a real vortex rather than a rigid rotation.

3. **Inward pull** — each feather periodically drifts toward the center and back out. When closer to center: smaller size, lower opacity. Creates the funnel/drain effect.

4. **Depth sorting** — every frame, feathers are sorted by their current radius. Feathers closer to center are drawn first (behind), outer feathers drawn on top.

5. **Opacity breathing** — each feather pulses in opacity on its own rhythm, independent of position.

### Vortex Setup Function

```javascript
function createVortex(canvas, featherCount, sizeMultiplier) {
  var ctx = canvas.getContext('2d');
  var w = canvas.width;
  var h = canvas.height;
  var cx = w / 2;
  var cy = h / 2;
  var maxR = w * 0.36;
  var baseSize = (w / 128) * sizeMultiplier;

  var feathers = [];
  for (var i = 0; i < featherCount; i++) {
    feathers.push({
      angle: (i / featherCount) * Math.PI * 2 + Math.random() * 0.3,
      speed: 0.35 + Math.random() * 0.7,
      baseR: 0.45 + Math.random() * 0.45,
      rPhase: Math.random() * Math.PI * 2,
      rFreq: 0.25 + Math.random() * 0.5,
      rAmp: 0.1 + Math.random() * 0.18,
      size: (0.7 + Math.random() * 0.6) * baseSize,
      opBase: 0.25 + (1 - i / featherCount) * 0.65,
      opPhase: Math.random() * Math.PI * 2,
      opFreq: 0.3 + Math.random() * 0.5,
      tiltOffset: (Math.random() - 0.5) * 0.35,
      inwardPhase: Math.random() * Math.PI * 2,
      inwardFreq: 0.12 + Math.random() * 0.25
    });
  }

  var t = Math.random() * 100; // random start so multiple instances don't sync

  function draw() {
    ctx.clearRect(0, 0, w, h);
    t += 0.016;

    // Sort by radius for depth (inner = behind)
    var sorted = feathers.slice().sort(function(a, b) {
      var rA = a.baseR + Math.sin(t * a.rFreq + a.rPhase) * a.rAmp;
      var rB = b.baseR + Math.sin(t * b.rFreq + b.rPhase) * b.rAmp;
      return rA - rB;
    });

    for (var i = 0; i < sorted.length; i++) {
      var f = sorted[i];
      f.angle += f.speed * 0.016;

      var inwardPull = Math.sin(t * f.inwardFreq + f.inwardPhase);
      var rNorm = f.baseR + Math.sin(t * f.rFreq + f.rPhase) * f.rAmp + inwardPull * 0.12;
      rNorm = Math.max(0.18, Math.min(1.0, rNorm));
      var r = rNorm * maxR;

      var x = cx + Math.cos(f.angle) * r;
      var y = cy + Math.sin(f.angle) * r;

      // Tangent to orbit + slight variation + inward tilt when pulled in
      var tangent = f.angle + Math.PI / 2 + f.tiltOffset + inwardPull * 0.2;

      // Depth scaling — closer to center = smaller
      var depthScale = 0.45 + rNorm * 0.55;
      var drawSize = f.size * depthScale;

      var opacity = f.opBase + Math.sin(t * f.opFreq + f.opPhase) * 0.2;
      opacity *= (0.4 + rNorm * 0.6);
      opacity = Math.max(0.05, Math.min(0.95, opacity));

      drawFeather(ctx, x, y, drawSize, tangent, opacity);
    }

    requestAnimationFrame(draw);
  }

  draw();
}
```

### Recommended Configurations by Size

| Use case | Canvas size | CSS size | Feather count | Size multiplier |
|----------|------------|----------|---------------|-----------------|
| Loading screen | 320×320 | 160px | 13 | 5.5 |
| Chat indicator | 192×192 | 96px | 13 | 5.5 |
| Inline (next to text) | 128×128 | 40px | 10 | 7.0 |
| Compact inline | 96×96 | 28px | 8 | 7.5 |

The canvas is always rendered at 2x–3x the CSS size for sharpness on retina displays.

### Color

Feather fill: `#e6edf3` (matches `--text-primary` on dark theme)
Spine stroke: `rgba(139, 148, 158, 0.5)` (matches `--text-secondary`)
Barb strokes: `rgba(139, 148, 158, 0.6)`

These colors work on the Munin dark background (`#0f1419`). For light theme support, swap to darker feather colors — but the current platform is dark-only.

### Usage Example

```html
<div style="display: flex; align-items: center; gap: 12px;">
  <canvas id="thinking-spinner" width="128" height="128" style="width: 40px; height: 40px;"></canvas>
  <span style="color: #8b949e; font-size: 14px;">Thinking...</span>
</div>

<script>
  createVortex(document.getElementById('thinking-spinner'), 10, 7.0);
</script>
```

### Cleanup

The animation runs via `requestAnimationFrame`. To stop it (e.g., when thinking is done), the function should return a cleanup handle:

```javascript
function createVortex(canvas, featherCount, sizeMultiplier) {
  // ... setup ...
  var animId;

  function draw() {
    // ... render ...
    animId = requestAnimationFrame(draw);
  }

  draw();

  return function stop() {
    cancelAnimationFrame(animId);
    ctx.clearRect(0, 0, w, h);
  };
}

// Usage:
var stop = createVortex(canvas, 10, 7.0);
// Later, when done thinking:
stop();
```

### File Location

Save as `static/shared/feather-vortex.js` (or as a React component in `frontend/src/components/FeatherVortex.tsx` when building the chat UI). The static pages (research, search) can import the JS file directly for their loading states.
