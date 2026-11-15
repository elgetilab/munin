"""
Figure manifest hook. The SOURCE of this file is executed inside each kernel at
start (kernel.py reads it and runs it silently); it is never imported by the
service itself, so it must stay self-contained and depend only on what the
kernel has (matplotlib, numpy, IPython).

What it does: every time user code saves a matplotlib figure (Figure.savefig,
plt.savefig) or displays one inline, it walks the figure's artists and
publishes a JSON description of it as a display_data message with the
FIGURE_MIME type. The kernel manager captures that message and attaches the
description to the artifact the figure became. Nothing is printed and user
code sees no difference; any failure inside the hook is swallowed, because a
broken manifest must never break a user's savefig.

The description is what the evals grade against (axes scales, series data,
error bars, sizes, layout problems) and what the model gets back as a short
summary after each run, so it describes what was drawn, not what the code
intended.
"""

FIGURE_MIME = "application/vnd.munin.figure+json"
MAX_POINTS = 5000          # per series; longer arrays are summarised
MAX_SERIES = 200           # per axes
MAX_TEXTS = 200            # per axes


def _install():
    import math
    import os

    import numpy as np
    import matplotlib
    from matplotlib import colors as mcolors
    from matplotlib.figure import Figure
    from matplotlib.container import BarContainer, ErrorbarContainer
    from matplotlib.collections import LineCollection, PathCollection, PolyCollection

    try:
        from matplotlib.contour import ContourSet
    except Exception:  # pragma: no cover
        ContourSet = ()

    def _hex(c):
        try:
            return mcolors.to_hex(c, keep_alpha=False)
        except Exception:
            return None

    def _num(v):
        try:
            f = float(v)
        except Exception:
            return None
        return f if math.isfinite(f) else None

    def _arr(values):
        """A list of numbers, or a summary when the array is long."""
        try:
            a = np.asarray(values, dtype=float).ravel()
        except Exception:
            try:
                return {"n": len(values), "non_numeric": True}
            except Exception:
                return None
        if a.size <= MAX_POINTS:
            return [_num(v) for v in a]
        fin = a[np.isfinite(a)]
        return {
            "n": int(a.size),
            "truncated": True,
            "min": _num(fin.min()) if fin.size else None,
            "max": _num(fin.max()) if fin.size else None,
            "mean": _num(fin.mean()) if fin.size else None,
            "head": [_num(v) for v in a[:20]],
            "tail": [_num(v) for v in a[-20:]],
        }

    def _texts(artists):
        out = []
        for t in artists[:MAX_TEXTS]:
            try:
                s = t.get_text()
                if s and t.get_visible():
                    out.append(s)
            except Exception:
                pass
        return out

    def _errorbar(container):
        data_line, caplines, barcols = container.lines
        entry = {"kind": "errorbar", "label": _label(container.get_label())}
        if data_line is not None:
            xy = data_line.get_xydata()
            entry["x"] = _arr(xy[:, 0])
            entry["y"] = _arr(xy[:, 1])
            entry["color"] = _hex(data_line.get_color())
            entry["marker"] = str(data_line.get_marker())
            entry["linestyle"] = str(data_line.get_linestyle())
        else:
            xy = None
        # matplotlib draws y error bars as vertical segments and x error bars as
        # horizontal ones, one LineCollection each. Recover the extents per
        # point relative to the data point.
        for col in barcols or []:
            try:
                segs = col.get_segments()
            except Exception:
                continue
            if not segs:
                continue
            vertical = all(abs(s[0][0] - s[-1][0]) <= abs(s[0][1] - s[-1][1]) for s in segs)
            lo, hi = [], []
            for i, s in enumerate(segs):
                a = np.asarray(s, dtype=float)
                if vertical:
                    centre = xy[i, 1] if xy is not None and i < len(xy) else a[:, 1].mean()
                    lo.append(centre - a[:, 1].min())
                    hi.append(a[:, 1].max() - centre)
                else:
                    centre = xy[i, 0] if xy is not None and i < len(xy) else a[:, 0].mean()
                    lo.append(centre - a[:, 0].min())
                    hi.append(a[:, 0].max() - centre)
            key = "yerr" if vertical else "xerr"
            if np.allclose(lo, hi, rtol=1e-6, atol=1e-12):
                entry[key] = _arr(hi)
            else:
                entry[key + "_low"] = _arr(lo)
                entry[key + "_high"] = _arr(hi)
        return entry

    def _bar(container):
        patches = list(container.patches)
        horizontal = getattr(container, "orientation", "vertical") == "horizontal"
        pos, val, cols = [], [], []
        for p in patches:
            x, y, w, h = p.get_x(), p.get_y(), p.get_width(), p.get_height()
            if horizontal:
                pos.append(y + h / 2.0)
                val.append(w)
            else:
                pos.append(x + w / 2.0)
                val.append(h)
            cols.append(_hex(p.get_facecolor()))
        entry = {
            "kind": "bar",
            "orientation": "horizontal" if horizontal else "vertical",
            "label": _label(container.get_label()),
            "positions": _arr(pos),
            "values": _arr(val),
            "colors": sorted({c for c in cols if c}),
        }
        if container.errorbar is not None:
            eb = _errorbar(container.errorbar)
            for k in ("yerr", "yerr_low", "yerr_high", "xerr", "xerr_low", "xerr_high"):
                if k in eb:
                    entry[k] = eb[k]
        return entry

    def _label(s):
        if not s or str(s).startswith("_"):
            return None
        return str(s)

    def _in_data(artist, ax):
        """Drawn in data coordinates on both axes (axvline/axhline are not)."""
        try:
            return artist.get_transform().contains_branch(ax.transData)
        except Exception:
            return False

    def _stem(line, vertical):
        """(position, lo, hi, spread) when the line is one error stem: a straight
        segment at one position, plus optional caps at its two ends (seaborn
        draws each bar's error as a separate Line2D, capsize adds the caps)."""
        xy = np.asarray(line.get_xydata(), dtype=float)
        xy = xy[np.isfinite(xy).all(axis=1)] if xy.ndim == 2 else xy[:0]
        if len(xy) < 2:
            return None
        p, v = (xy[:, 0], xy[:, 1]) if vertical else (xy[:, 1], xy[:, 0])
        pos = float(np.median(p))
        on = np.isclose(p, pos, rtol=0, atol=1e-9 * max(1.0, abs(pos)))
        if on.sum() < 2:
            return None
        lo, hi = float(v[on].min()), float(v[on].max())
        if hi <= lo:
            return None
        tol = 1e-9 * max(1.0, abs(hi))
        ends = np.isclose(v[~on], lo, rtol=0, atol=tol) | np.isclose(v[~on], hi, rtol=0, atol=tol)
        if not ends.all():
            return None
        return pos, lo, hi, float(np.ptp(p))

    def _err_keys(axis, lo, hi):
        if all(a is None for a in lo):
            return {}
        sym = all(a is None or abs(a - b) <= 1e-6 * max(1.0, abs(b)) for a, b in zip(lo, hi))
        return {axis: _arr(hi)} if sym else {axis + "_low": _arr(lo), axis + "_high": _arr(hi)}

    def _bar_errors(ax, bars, claimed):
        """Error bars drawn as loose Line2D stems over bars (seaborn barplot):
        a stem counts when it sits on a bar centre and spans the bar tip."""
        cand = [l for l in ax.get_lines() if id(l) not in claimed and _in_data(l, ax)]
        extra = []
        for entry, c in bars:
            if any(k in entry for k in ("yerr", "yerr_low", "xerr", "xerr_low")):
                continue
            vertical = entry["orientation"] == "vertical"
            lo_e, hi_e, used = [], [], []
            for p in c.patches:
                if vertical:
                    centre, width = p.get_x() + p.get_width() / 2.0, abs(p.get_width())
                    tip = p.get_y() + p.get_height()
                else:
                    centre, width = p.get_y() + p.get_height() / 2.0, abs(p.get_height())
                    tip = p.get_x() + p.get_width()
                hit = None
                for l in cand:
                    st = _stem(l, vertical)
                    if st is None:
                        continue
                    pos, lo, hi, spread = st
                    tol = 1e-6 * max(1.0, abs(tip))
                    if (abs(pos - centre) <= 1e-6 * max(width, 1e-12) and spread <= width + 1e-9
                            and lo - tol <= tip <= hi + tol):
                        hit = (l, tip - lo, hi - tip)
                        break
                if hit is None:
                    lo_e.append(None); hi_e.append(None)
                else:
                    cand.remove(hit[0]); used.append(hit[0])
                    lo_e.append(max(hit[1], 0.0)); hi_e.append(max(hit[2], 0.0))
            errs = _err_keys("yerr" if vertical else "xerr", lo_e, hi_e)
            if errs:
                entry.update(errs)
                extra.append(dict({"kind": "errorbar", "label": None}, **errs))
                claimed.update(id(l) for l in used)
        return extra

    def _point_errors(ax, claimed):
        """seaborn pointplot: a marker line plus one same-coloured vertical stem
        per point. Returns {id(anchor line): error keys} and claims the stems."""
        lines = [l for l in ax.get_lines() if id(l) not in claimed and _in_data(l, ax)]
        stems = {id(l): _stem(l, True) for l in lines}
        out = {}
        for a in lines:
            if stems[id(a)] is not None:
                continue
            xy = np.asarray(a.get_xydata(), dtype=float)
            if xy.ndim != 2 or not len(xy):
                continue
            col = _hex(a.get_color())
            # Caps (capsize) give a stem some width; allow it up to the gap
            # between neighbouring points so a stem never spans two of them.
            xs = np.unique(xy[:, 0][np.isfinite(xy[:, 0])])
            max_spread = float(np.diff(xs).min()) if xs.size > 1 else 1.0
            lo_e, hi_e, used = [], [], []
            for x, y in xy:
                hit = None
                for l in lines:
                    st = stems[id(l)]
                    if st is None or id(l) in claimed or l in used or _hex(l.get_color()) != col:
                        continue
                    pos, lo, hi, spread = st
                    if (abs(pos - x) <= 1e-9 * max(1.0, abs(x)) and spread < max_spread
                            and lo <= y <= hi):
                        hit = (l, y - lo, hi - y)
                        break
                if hit is None:
                    lo_e.append(None); hi_e.append(None)
                else:
                    used.append(hit[0]); lo_e.append(hit[1]); hi_e.append(hit[2])
            errs = _err_keys("yerr", lo_e, hi_e)
            if errs:
                out[id(a)] = errs
                claimed.update(id(l) for l in used)
        return out

    def _band_errors(ax, claimed):
        """fill_between band around a line of the same colour (seaborn lineplot):
        per x, the band's lowest and highest vertex. Claims the band."""
        out = {}
        lines = [l for l in ax.get_lines() if id(l) not in claimed and _in_data(l, ax)]
        for col in ax.collections:
            # Exactly what fill_between returns (PolyCollection, or its
            # FillBetweenPolyCollection subclass from matplotlib 3.10), not
            # other subclasses such as pcolor's PolyQuadMesh.
            if (id(col) in claimed
                    or type(col).__name__ not in ("PolyCollection", "FillBetweenPolyCollection")
                    or not _in_data(col, ax) or len(col.get_paths()) != 1):
                continue
            v = np.asarray(col.get_paths()[0].vertices, dtype=float)
            fcs = {_hex(c) for c in col.get_facecolors()}
            for l in lines:
                if id(l) in out or _hex(l.get_color()) not in fcs:
                    continue
                xy = np.asarray(l.get_xydata(), dtype=float)
                if xy.ndim != 2 or len(xy) < 2:
                    continue
                lo_e, hi_e, ok = [], [], True
                for x, y in xy:
                    at = v[np.isclose(v[:, 0], x, rtol=0, atol=1e-9 * max(1.0, abs(x))), 1]
                    if at.size < 2 or not (at.min() <= y <= at.max()):
                        ok = False
                        break
                    lo_e.append(y - at.min()); hi_e.append(at.max() - y)
                if ok:
                    out[id(l)] = dict(_err_keys("yerr", lo_e, hi_e), error_style="band")
                    claimed.add(id(col))
                    break
        return out

    def _axes(ax):
        info = {
            "title": ax.get_title() or None,
            "xlabel": ax.get_xlabel() or None,
            "ylabel": ax.get_ylabel() or None,
            "xscale": ax.get_xscale(),
            "yscale": ax.get_yscale(),
            "xlim": [_num(v) for v in ax.get_xlim()],
            "ylim": [_num(v) for v in ax.get_ylim()],
            "x_inverted": bool(ax.xaxis_inverted()),
            "y_inverted": bool(ax.yaxis_inverted()),
            "xticklabels": _texts(ax.get_xticklabels()),
            "yticklabels": _texts(ax.get_yticklabels()),
            "series": [],
            "texts": _texts(ax.texts),
        }
        leg = ax.get_legend()
        info["legend"] = _texts(leg.get_texts()) if leg is not None else None

        claimed = set()  # artists already described as part of a container
        bars = []
        for c in ax.containers:
            try:
                if isinstance(c, ErrorbarContainer):
                    info["series"].append(_errorbar(c))
                elif isinstance(c, BarContainer):
                    info["series"].append(_bar(c))
                    bars.append((info["series"][-1], c))
                    if c.errorbar is not None:
                        claimed.update(id(a) for a in _flatten(c.errorbar))
                else:
                    continue
                claimed.update(id(a) for a in _flatten(c))
            except Exception:
                pass

        point_errs = band_errs = {}
        try:
            info["series"].extend(_bar_errors(ax, bars, claimed))
            point_errs = _point_errors(ax, claimed)
            band_errs = _band_errors(ax, claimed)
        except Exception:
            pass

        for line in ax.get_lines():
            if id(line) in claimed or len(info["series"]) >= MAX_SERIES:
                continue
            try:
                xy = line.get_xydata()
                if not len(xy):
                    continue  # seaborn's empty legend proxies
                errs = point_errs.get(id(line)) or band_errs.get(id(line)) or {}
                info["series"].append(dict({
                    "kind": "line" if str(line.get_linestyle()) not in ("None", "", " ")
                    else "points",
                    "label": _label(line.get_label()),
                    "color": _hex(line.get_color()),
                    "linestyle": str(line.get_linestyle()),
                    "marker": str(line.get_marker()),
                    "x": _arr(xy[:, 0]),
                    "y": _arr(xy[:, 1]),
                }, **errs))
                if id(line) in point_errs:
                    info["series"][-1]["kind"] = "errorbar"
            except Exception:
                pass

        for col in ax.collections:
            if id(col) in claimed or len(info["series"]) >= MAX_SERIES:
                continue
            try:
                if ContourSet and isinstance(col, ContourSet):
                    info["series"].append({
                        "kind": "contour",
                        "label": _label(col.get_label()),
                        "levels": _arr(col.levels),
                        "filled": bool(getattr(col, "filled", False)),
                    })
                elif isinstance(col, PathCollection):
                    off = np.asarray(col.get_offsets(), dtype=float)
                    fc = col.get_facecolors()
                    info["series"].append({
                        "kind": "scatter",
                        "label": _label(col.get_label()),
                        "x": _arr(off[:, 0]) if off.size else [],
                        "y": _arr(off[:, 1]) if off.size else [],
                        "colors": sorted({_hex(c) for c in fc[:MAX_POINTS]} - {None}),
                    })
                elif isinstance(col, LineCollection):
                    info["series"].append({
                        "kind": "segments",
                        "label": _label(col.get_label()),
                        "n": len(col.get_segments()),
                    })
                elif isinstance(col, PolyCollection):
                    info["series"].append({
                        "kind": "fill",
                        "label": _label(col.get_label()),
                        "n": len(col.get_paths()),
                        "colors": sorted({_hex(c) for c in col.get_facecolors()} - {None}),
                    })
            except Exception:
                pass

        for im in ax.images:
            try:
                arr = im.get_array()
                info["series"].append({
                    "kind": "image",
                    "shape": list(np.shape(arr)) if arr is not None else None,
                    "cmap": getattr(im.get_cmap(), "name", None),
                    "clim": [_num(v) for v in im.get_clim()],
                })
            except Exception:
                pass
        return info

    def _flatten(c):
        out = []
        for a in c:
            if isinstance(a, (list, tuple)):
                out.extend(_flatten(a))
            elif a is not None:
                out.append(a)
        return out

    def _drawn_ticklabels(axis):
        """Major tick labels matplotlib actually draws: ticks outside the view
        limits get a label text from the formatter but are never drawn, so
        they must not count as clipped or overlapping."""
        try:
            locs = list(axis.get_majorticklocs())
            ticks = axis.get_major_ticks(len(locs))
            lo, hi = sorted(axis.get_view_interval())
        except Exception:
            return []
        tol = 1e-9 * max(abs(lo), abs(hi), hi - lo, 1e-300)
        out = []
        for loc, tick in zip(locs, ticks):
            try:
                if not (lo - tol <= float(loc) <= hi + tol):
                    continue
                out.extend(t for t in (tick.label1, tick.label2) if t.get_visible())
            except Exception:
                pass
        return out

    def _layout(fig, renderer=None):
        """Text that sticks out of the figure or overlaps other text, and
        legends that cover data. Needs a drawn figure. Called from the
        draw_event of a savefig, renderer, fig.dpi and fig.bbox are the saved
        file's (its dpi, and the tight box when bbox_inches='tight')."""
        out = {"clipped_text": [], "overlapping_text": [], "legend_covers_points": 0}
        if renderer is None:
            try:
                renderer = fig.canvas.get_renderer()
            except Exception:
                return out
        fb = fig.bbox
        boxes = []
        for ax in fig.axes:
            groups = [
                ("title", [ax.title, getattr(ax, "_left_title", None),
                           getattr(ax, "_right_title", None)]),
                ("xlabel", [ax.xaxis.label]),
                ("ylabel", [ax.yaxis.label]),
                ("xtick", _drawn_ticklabels(ax.xaxis)),
                ("ytick", _drawn_ticklabels(ax.yaxis)),
                ("text", list(ax.texts)),
            ]
            for kind, arts in groups:
                for t in arts:
                    try:
                        if t is None or not t.get_visible() or not t.get_text():
                            continue
                        bb = t.get_window_extent(renderer)
                    except Exception:
                        continue
                    boxes.append((kind, t.get_text(), bb))
                    if (bb.x0 < fb.x0 - 1 or bb.y0 < fb.y0 - 1
                            or bb.x1 > fb.x1 + 1 or bb.y1 > fb.y1 + 1):
                        if len(out["clipped_text"]) < 20:
                            out["clipped_text"].append(t.get_text()[:60])
            leg = ax.get_legend()
            if leg is not None:
                try:
                    lb = leg.get_window_extent(renderer)
                    pts = []
                    for line in ax.get_lines():
                        pts.append(ax.transData.transform(line.get_xydata()))
                    for col in ax.collections:
                        if isinstance(col, PathCollection):
                            pts.append(ax.transData.transform(col.get_offsets()))
                    n = 0
                    for p in pts:
                        p = np.asarray(p, dtype=float)
                        if p.size:
                            n += int(((p[:, 0] > lb.x0) & (p[:, 0] < lb.x1)
                                      & (p[:, 1] > lb.y0) & (p[:, 1] < lb.y1)).sum())
                    out["legend_covers_points"] += n
                except Exception:
                    pass
        # Overlaps under one renderer unit are ignored: a pixel of the saved
        # file, or a point for vector formats (72 units per inch).
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i][2], boxes[j][2]
                if a.overlaps(b):
                    inter_w = min(a.x1, b.x1) - max(a.x0, b.x0)
                    inter_h = min(a.y1, b.y1) - max(a.y0, b.y0)
                    if inter_w > 1 and inter_h > 1 and len(out["overlapping_text"]) < 20:
                        out["overlapping_text"].append(
                            [boxes[i][1][:40], boxes[j][1][:40]])
        return out

    def describe(fig, path=None, fmt=None, dpi=None, saved=None):
        """saved: what the savefig draw saw ({"size_in", "layout"}), so the
        description reports the file as written, not the figure's settings."""
        if dpi in (None, "figure"):
            sd = matplotlib.rcParams.get("savefig.dpi", "figure")
            dpi = fig.dpi if sd in (None, "figure") else sd
        w, h = fig.get_size_inches()
        desc = {
            "path": path,
            "format": fmt,
            "size_in": [_num(w), _num(h)],
            "size_mm": [_num(w * 25.4), _num(h * 25.4)],
            "dpi": _num(dpi),
            "suptitle": (fig._suptitle.get_text() if getattr(fig, "_suptitle", None) else None),
            "axes": [],
        }
        saved = saved or {}
        if saved.get("size_in"):
            sw, sh = saved["size_in"]
            desc["size_in_saved"] = [_num(sw), _num(sh)]
            desc["size_mm_saved"] = [_num(sw * 25.4), _num(sh * 25.4)]
            desc["dpi_saved"] = _num(dpi)
            if fmt in ("png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp", "gif", "raw", "rgba"):
                try:
                    # Agg truncates the canvas size to whole pixels.
                    desc["size_px_saved"] = [int(sw * dpi), int(sh * dpi)]
                except Exception:
                    pass
            # More than half a millimetre off the figsize: bbox_inches
            # ('tight' or a box) changed the page size.
            desc["size_differs_on_save"] = bool(
                abs(sw - w) * 25.4 > 0.5 or abs(sh - h) * 25.4 > 0.5)
        for ax in fig.axes:
            try:
                desc["axes"].append(_axes(ax))
            except Exception as exc:
                desc["axes"].append({"error": f"{type(exc).__name__}: {exc}"})
        desc["layout"] = saved.get("layout") or _layout(fig)
        return desc

    def _publish(desc):
        try:
            from IPython.display import publish_display_data
            publish_display_data({FIGURE_MIME: desc}, metadata={FIGURE_MIME: {"munin": True}})
        except Exception:
            pass

    original_savefig = Figure.savefig

    def savefig(self, fname, *args, **kwargs):
        # Measure while savefig draws: then the renderer has the saved dpi and
        # fig.bbox / fig.bbox_inches are the saved page (the tight box with
        # bbox_inches='tight'). Measuring afterwards used the figure's own dpi
        # and size and reported layout problems the file does not have. The
        # last draw wins (a tight save draws once to find the box first).
        saved = {}

        def _on_draw(event):
            try:
                bi = self.bbox_inches
                saved["size_in"] = (float(bi.width), float(bi.height))
                saved["layout"] = _layout(self, event.renderer)
            except Exception:
                pass

        cid = None
        try:
            cid = self.canvas.mpl_connect("draw_event", _on_draw)
        except Exception:
            pass
        try:
            result = original_savefig(self, fname, *args, **kwargs)
        finally:
            if cid is not None:
                try:
                    self.canvas.mpl_disconnect(cid)
                except Exception:
                    pass
        try:
            if isinstance(fname, (str, bytes, os.PathLike)):
                path = os.path.abspath(os.fsdecode(fname))
                fmt = kwargs.get("format")
                if not fmt:
                    ext = os.path.splitext(path)[1].lstrip(".").lower()
                    fmt = ext or matplotlib.rcParams.get("savefig.format", "png")
                    if not ext:
                        path = path + "." + fmt
                _publish(describe(self, path=path, fmt=fmt, dpi=kwargs.get("dpi"), saved=saved))
        except Exception:
            pass
        return result

    savefig.__doc__ = original_savefig.__doc__
    savefig.__wrapped__ = original_savefig
    Figure.savefig = savefig

    # Inline display: add the description to the same display_data bundle as
    # the PNG, so the kernel manager can attach it to the inline image.
    try:
        from IPython import get_ipython
        from IPython.core.formatters import JSONFormatter
        from traitlets import ObjectName, Unicode

        # JSONFormatter, not BaseFormatter: the base class only accepts str
        # return values and would drop the dict with a warning.
        class _FigureFormatter(JSONFormatter):
            format_type = Unicode(FIGURE_MIME)
            print_method = ObjectName("_repr_munin_figure_")

        ip = get_ipython()
        if ip is not None:
            df = ip.display_formatter
            fmt = _FigureFormatter(parent=df)
            df.formatters[FIGURE_MIME] = fmt

            def _inline(fig):  # becomes a method: fig is self
                try:
                    fig.canvas.draw()
                    return describe(fig, fmt="png")
                except Exception:
                    return None

            # Through the print method, not fmt.for_type(Figure, ...): the
            # inline backend's select_figure_formats() pops every Figure
            # printer from all formatters when pyplot is imported, which
            # would silently remove a type registration.
            Figure._repr_munin_figure_ = _inline
    except Exception:
        pass

    return describe


try:
    _munin_describe_figure = _install()
except Exception:  # never let the hook break kernel start
    _munin_describe_figure = None
