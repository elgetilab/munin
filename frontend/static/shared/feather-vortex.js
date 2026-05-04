/**
 * Munin Feather Vortex — Thinking/Loading Indicator
 *
 * Raven feathers caught in a vortex: they orbit at different speeds
 * (overtaking each other), breathe in and out toward the center,
 * and fade with depth.
 *
 * Usage:
 *   var stop = createVortex(canvasElement, featherCount, sizeMultiplier);
 *   // later: stop();
 *
 * Recommended configurations:
 *   Inline (40px):   createVortex(canvas, 10, 7.0)      — canvas 128x128, CSS 40px
 *   Loading (160px): createVortex(canvas, 13, 5.5)      — canvas 320x320, CSS 160px
 *   Sleeping (96px): createVortex(canvas, 10, 5.5, 0.3) — slow drift for off-hours
 */

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

function createVortex(canvas, featherCount, sizeMultiplier, speedMultiplier) {
  speedMultiplier = speedMultiplier || 1.0;
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

  var t = Math.random() * 100;
  var animId;

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
      f.angle += f.speed * 0.016 * speedMultiplier;

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

    animId = requestAnimationFrame(draw);
  }

  draw();

  return function stop() {
    cancelAnimationFrame(animId);
    ctx.clearRect(0, 0, w, h);
  };
}

/**
 * Loading message bank — organized by phase.
 */
var LOADING_MESSAGES = {
  thinking: [
    'Consulting the runes...',
    'Raven dispatched...',
    'Pondering in the mead hall...',
    'Unraveling the threads of thought...',
    'Sharpening the quill...',
    'Summoning ancient wisdom...',
    'Perched and pondering...',
    'Whispering to Odin\'s ear...',
    'Fluffing through the archives...',
    'Gathering scattered feathers...',
    'Descending into the knowledge well...',
    'Sifting through the sagas...',
    'Peering through the mist...',
    'Turning the hourglass...',
    'Stirring the cauldron of ideas...',
    'Reading the wind...',
    'Following a hunch...',
    'Meditating on the question...',
    'Tracing patterns in the frost...',
    'Listening to the echoes...',
    'Deciphering the old scripts...',
    'Weighing the possibilities...',
    'Gazing into the well of memory...',
    'Warming up the inkwell...',
    'Delegating to the scribes...',
    'Sending an apprentice to check...',
    'The acolytes are on it...',
    'Putting the scholars to work...',
    'The scribe insists this wasn\'t in the brief...'
  ],
  paper_search: [
    'Rifling through the scrolls...',
    'Interrogating the literature...',
    'Swooping through the stacks...',
    'Pecking at the knowledge graph...',
    'Cross-referencing citations...',
    'Following the paper trail...',
    'Diving into the archives...',
    'Chasing footnotes...',
    'Hunting through abstracts...',
    'Rummaging through the library...',
    'Scanning the indices...',
    'Tracing the reference chain...',
    'Leafing through journals...',
    'Querying the scholarly vaults...',
    'Excavating buried findings...',
    'Cataloguing the evidence...',
    'Browsing the stacks...',
    'Pulling threads from the literature...',
    'Comparing methodologies...',
    'Checking the bibliography...',
    'Dispatching the apprentices to the stacks...',
    'The scribes are pulling references...',
    'An acolyte is cross-checking the sources...',
    'Making a scholar read the supplementary materials...',
    'The scribe says the citation format is wrong again...'
  ],
  web_search: [
    'Scouring the nine realms...',
    'Sending ravens across the web...',
    'Foraging far and wide...',
    'Mapping the world tree...',
    'Flying reconnaissance...',
    'Circling the internet...',
    'Dispatching scouts...',
    'Casting a wide net...',
    'Navigating the branches of Yggdrasil...',
    'Probing distant shores...',
    'Sweeping the horizon...',
    'Trawling the depths...',
    'Following breadcrumbs...',
    'Charting unfamiliar waters...',
    'Surveying the landscape...',
    'Sending the apprentices out to forage...',
    'A scholar is fetching this from afar...'
  ],
  processing: [
    'Digesting the findings...',
    'Distilling the essence...',
    'Weaving the threads together...',
    'Arranging the feathers...',
    'Composing the report...',
    'Assembling the mosaic...',
    'Translating from raven to human...',
    'Connecting the dots...',
    'Polishing the summary...',
    'Sorting through the harvest...',
    'Crystallizing the insights...',
    'Forging the final draft...',
    'Stitching the narrative...',
    'Shaping the conclusions...',
    'Laying out the findings...',
    'The scribe is writing up the results...',
    'Making an apprentice proofread this...',
    'An acolyte is formatting the tables...',
    'The scholars are arguing about the wording...'
  ],
  code: [
    'Compiling incantations...',
    'Debugging the runes...',
    'Refactoring the spellbook...',
    'Optimizing the enchantment...',
    'Tracing the logic threads...',
    'Parsing the syntax trees...',
    'Evaluating expressions...',
    'Resolving dependencies...',
    'Linting the grimoire...',
    'Checking the type runes...',
    'Iterating through the loops...',
    'Unwinding the stack...',
    'Consulting the documentation scrolls...',
    'Running the test harness...',
    'Validating the spell...',
    'An apprentice is reviewing the logic...',
    'The scribe swears it compiled yesterday...'
  ],
  deep_research: [
    'This may take a few wingbeats...',
    'Embarking on a longer journey...',
    'The raven flies far for this one...',
    'Deep in the knowledge well...',
    'Thorough research takes time...',
    'Consulting the runes...',
    'Sifting through the sagas...',
    'Weaving the threads together...',
    'Distilling the essence...',
    'Composing the report...',
    'Mapping the full territory...',
    'Leaving no stone unturned...',
    'Exploring every branch...',
    'Gathering from many sources...',
    'Building the complete picture...',
    'Following every lead...',
    'Surveying the entire landscape...',
    'Piecing together the puzzle...',
    'Conducting a thorough sweep...',
    'Assembling all the evidence...',
    'Every apprentice in the hall is on this...',
    'The scribes have been at it for hours...',
    'Mobilizing the entire scriptorium...',
    'An acolyte just requested more candles...',
    'The scholars were promised mead after this...',
    'Three scribes, two apprentices, one deadline...'
  ]
};

/**
 * Start rotating loading messages in a target element.
 * Messages fade out, swap, fade in every 2.5 seconds.
 *
 * Usage:
 *   var stopMessages = createRotatingMessage(element, 'deep_research');
 *   // later: stopMessages();
 *
 * @param {HTMLElement} el - Element whose textContent will be updated
 * @param {string} phase - Key into LOADING_MESSAGES
 * @returns {function} stop - Call to stop the rotation
 */
function createRotatingMessage(el, phase) {
  var messages = LOADING_MESSAGES[phase] || LOADING_MESSAGES.thinking;
  var last = -1;
  var typeTimer = null;
  var cycleTimer = null;
  var stopped = false;

  function pick() {
    var idx;
    do {
      idx = Math.floor(Math.random() * messages.length);
    } while (idx === last && messages.length > 1);
    last = idx;
    return messages[idx];
  }

  /** Type out text one character at a time, then call onDone. */
  function typeOut(text, onDone) {
    var i = 0;
    el.textContent = '';
    el.style.opacity = '1';
    typeTimer = setInterval(function() {
      if (stopped) return;
      i++;
      el.textContent = text.slice(0, i);
      if (i >= text.length) {
        clearInterval(typeTimer);
        typeTimer = null;
        if (onDone) onDone();
      }
    }, 35);
  }

  /** Schedule next message: wait, fade out, then type new one. */
  function scheduleNext() {
    cycleTimer = setTimeout(function() {
      if (stopped) return;
      el.style.transition = 'opacity 0.2s';
      el.style.opacity = '0';
      setTimeout(function() {
        if (stopped) return;
        typeOut(pick(), scheduleNext);
      }, 220);
    }, 2200);
  }

  el.style.transition = 'none';
  typeOut(pick(), scheduleNext);

  return function stop() {
    stopped = true;
    if (typeTimer) clearInterval(typeTimer);
    if (cycleTimer) clearTimeout(cycleTimer);
  };
}
