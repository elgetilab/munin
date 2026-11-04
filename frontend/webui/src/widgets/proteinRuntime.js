// Runs inside the sandboxed widget frame, beside 3Dmol.js. It has no network and
// no access to the app: the structure and every setting arrive by postMessage,
// and they are treated as data.
(function () {
  var NON_POLYMER_SOLVENT = ['HOH', 'WAT', 'DOD'];
  var viewer = null;
  var currentKey = null;
  var currentReset = 0;
  var residueRange = [0, 1];

  function send(message) {
    window.parent.postMessage(message, '*');
  }

  // Blue at the N terminus through to red at the C terminus.
  function spectrumColor(atom) {
    var span = residueRange[1] - residueRange[0] || 1;
    var t = Math.min(1, Math.max(0, (atom.resi - residueRange[0]) / span));
    return hslToHex((1 - t) * 240);
  }

  function hslToHex(hue) {
    var c = 0.85;
    var x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
    var rgb = hue < 60 ? [c, x, 0] : hue < 120 ? [x, c, 0] : hue < 180 ? [0, c, x] : [0, x, c];
    var m = 0.08;
    return rgb.reduce(function (hex, v) {
      return hex * 256 + Math.round((v + m) * 255);
    }, 0);
  }

  // The bands AlphaFold publishes for per-residue confidence (pLDDT).
  function confidenceColor(atom) {
    if (atom.b >= 90) return 0x0053d6;
    if (atom.b >= 70) return 0x65cbf3;
    if (atom.b >= 50) return 0xffdb13;
    return 0xff7d45;
  }

  function bfactorColor(atom) {
    var t = Math.min(1, Math.max(0, atom.b / 100));
    return hslToHex((1 - t) * 240);
  }

  function colorOptions(state) {
    if (state.color === 'chain') return { colorscheme: 'chain' };
    if (state.color === 'secondary') return { colorscheme: 'ssPyMol' };
    if (state.color === 'bfactor') {
      return { colorfunc: state.source === 'alphafold' ? confidenceColor : bfactorColor };
    }
    return { colorfunc: spectrumColor };
  }

  function drawStyle(state) {
    var color = colorOptions(state);
    if (state.style === 'stick') return { stick: Object.assign({ radius: 0.15 }, color) };
    if (state.style === 'sphere') return { sphere: Object.assign({}, color) };
    if (state.style === 'line') return { line: Object.assign({}, color) };
    return { cartoon: Object.assign({}, color) };
  }

  function loadStructure(structure) {
    viewer.removeAllModels();
    var model = viewer.addModel(structure.data, structure.format);
    var residues = model
      .selectedAtoms({ hetflag: false })
      .map(function (atom) { return atom.resi; });
    residueRange = residues.length
      ? [Math.min.apply(null, residues.slice(0, 100000)), Math.max.apply(null, residues.slice(0, 100000))]
      : [0, 1];
    currentKey = structure.key;
    return model.selectedAtoms({}).length;
  }

  function apply(state) {
    if (!viewer) {
      viewer = window.$3Dmol.createViewer(document.getElementById('viewer'), {
        backgroundColor: '#0f1419',
      });
      if (!viewer) throw new Error('This browser cannot show 3D graphics (WebGL is unavailable).');
    }
    var atoms = null;
    var isNew = state.structure && state.structure.key !== currentKey;
    if (isNew) atoms = loadStructure(state.structure);
    if (currentKey === null) return;

    viewer.setStyle({}, {});
    viewer.setStyle({ hetflag: false }, drawStyle(state));
    viewer.setStyle(
      { hetflag: true, not: { resn: NON_POLYMER_SOLVENT } },
      { stick: { radius: 0.2, colorscheme: 'greenCarbon' } },
    );
    (state.highlight || []).forEach(function (item) {
      var selection = { resi: item.residues };
      if (item.chain) selection.chain = item.chain;
      viewer.addStyle(selection, { stick: { radius: 0.25, colorscheme: 'magentaCarbon' } });
    });

    if (isNew || state.reset !== currentReset) {
      currentReset = state.reset;
      viewer.zoomTo();
    }
    viewer.spin(state.spin ? 'y' : false, 0.6);
    viewer.render();
    send({ type: 'rendered', atoms: atoms });
  }

  window.addEventListener('message', function (event) {
    if (event.source !== window.parent || !event.data || event.data.type !== 'state') return;
    try {
      apply(event.data.state);
    } catch (error) {
      send({ type: 'error', message: String((error && error.message) || error) });
    }
  });
  window.addEventListener('resize', function () {
    if (viewer) viewer.resize();
  });
  send({ type: 'ready' });
})();
