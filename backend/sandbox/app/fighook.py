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
        for c in ax.containers:
            try:
                if isinstance(c, ErrorbarContainer):
                    info["series"].append(_errorbar(c))
                elif isinstance(c, BarContainer):
                    info["series"].append(_bar(c))
                    if c.errorbar is not None:
                        claimed.update(id(a) for a in _flatten(c.errorbar))
                else:
                    continue
                claimed.update(id(a) for a in _flatten(c))
            except Exception:
                pass

        for line in ax.get_lines():
            if id(line) in claimed or len(info["series"]) >= MAX_SERIES:
                continue
            try:
                xy = line.get_xydata()
                info["series"].append({
                    "kind": "line" if str(line.get_linestyle()) not in ("None", "", " ")
                    else "points",
                    "label": _label(line.get_label()),
                    "color": _hex(line.get_color()),
                    "linestyle": str(line.get_linestyle()),
                    "marker": str(line.get_marker()),
                    "x": _arr(xy[:, 0]),
                    "y": _arr(xy[:, 1]),
                })
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

    def _layout(fig):
        """Text that sticks out of the figure or overlaps other text, and
        legends that cover data. Needs a drawn figure (savefig has drawn it)."""
        out = {"clipped_text": [], "overlapping_text": [], "legend_covers_points": 0}
        try:
            renderer = fig.canvas.get_renderer()
        except Exception:
            return out
        fb = fig.bbox
        boxes = []
        for ax in fig.axes:
            groups = [
                ("title", [ax.title]),
                ("xlabel", [ax.xaxis.label]),
                ("ylabel", [ax.yaxis.label]),
                ("xtick", ax.get_xticklabels()),
                ("ytick", ax.get_yticklabels()),
                ("text", list(ax.texts)),
            ]
            for kind, arts in groups:
                for t in arts:
                    try:
                        if not t.get_visible() or not t.get_text():
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

    def describe(fig, path=None, fmt=None, dpi=None):
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
        for ax in fig.axes:
            try:
                desc["axes"].append(_axes(ax))
            except Exception as exc:
                desc["axes"].append({"error": f"{type(exc).__name__}: {exc}"})
        desc["layout"] = _layout(fig)
        return desc

    def _publish(desc):
        try:
            from IPython.display import publish_display_data
            publish_display_data({FIGURE_MIME: desc}, metadata={FIGURE_MIME: {"munin": True}})
        except Exception:
            pass

    original_savefig = Figure.savefig

    def savefig(self, fname, *args, **kwargs):
        result = original_savefig(self, fname, *args, **kwargs)
        try:
            if isinstance(fname, (str, bytes, os.PathLike)):
                path = os.path.abspath(os.fsdecode(fname))
                fmt = kwargs.get("format")
                if not fmt:
                    ext = os.path.splitext(path)[1].lstrip(".").lower()
                    fmt = ext or matplotlib.rcParams.get("savefig.format", "png")
                    if not ext:
                        path = path + "." + fmt
                _publish(describe(self, path=path, fmt=fmt, dpi=kwargs.get("dpi")))
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
