"""The 27 NIST StRD nonlinear regression models, from the published
definitions (https://www.itl.nist.gov/div898/strd/nls/nls_main.shtml).
Certified values and data are read from the certificate files at test time.
x is a 1D array, except Nelson (x = (x1, x2))."""
import numpy as np

e, pi = np.exp, np.pi

MODELS = {
    "Misra1a": lambda x, b1, b2: b1 * (1 - e(-b2 * x)),
    "Chwirut2": lambda x, b1, b2, b3: e(-b1 * x) / (b2 + b3 * x),
    "Chwirut1": lambda x, b1, b2, b3: e(-b1 * x) / (b2 + b3 * x),
    "Lanczos3": lambda x, b1, b2, b3, b4, b5, b6: b1 * e(-b2 * x) + b3 * e(-b4 * x) + b5 * e(-b6 * x),
    "Gauss1": lambda x, b1, b2, b3, b4, b5, b6, b7, b8: b1 * e(-b2 * x) + b3 * e(-(x - b4) ** 2 / b5 ** 2) + b6 * e(-(x - b7) ** 2 / b8 ** 2),
    "Gauss2": lambda x, b1, b2, b3, b4, b5, b6, b7, b8: b1 * e(-b2 * x) + b3 * e(-(x - b4) ** 2 / b5 ** 2) + b6 * e(-(x - b7) ** 2 / b8 ** 2),
    "DanWood": lambda x, b1, b2: b1 * x ** b2,
    "Misra1b": lambda x, b1, b2: b1 * (1 - (1 + b2 * x / 2) ** (-2)),
    "Kirby2": lambda x, b1, b2, b3, b4, b5: (b1 + b2 * x + b3 * x ** 2) / (1 + b4 * x + b5 * x ** 2),
    "Hahn1": lambda x, b1, b2, b3, b4, b5, b6, b7: (b1 + b2 * x + b3 * x ** 2 + b4 * x ** 3) / (1 + b5 * x + b6 * x ** 2 + b7 * x ** 3),
    "Nelson": lambda x, b1, b2, b3: b1 - b2 * x[0] * e(-b3 * x[1]),
    "MGH17": lambda x, b1, b2, b3, b4, b5: b1 + b2 * e(-x * b4) + b3 * e(-x * b5),
    "Lanczos1": lambda x, b1, b2, b3, b4, b5, b6: b1 * e(-b2 * x) + b3 * e(-b4 * x) + b5 * e(-b6 * x),
    "Lanczos2": lambda x, b1, b2, b3, b4, b5, b6: b1 * e(-b2 * x) + b3 * e(-b4 * x) + b5 * e(-b6 * x),
    "Gauss3": lambda x, b1, b2, b3, b4, b5, b6, b7, b8: b1 * e(-b2 * x) + b3 * e(-(x - b4) ** 2 / b5 ** 2) + b6 * e(-(x - b7) ** 2 / b8 ** 2),
    "Misra1c": lambda x, b1, b2: b1 * (1 - (1 + 2 * b2 * x) ** (-0.5)),
    "Misra1d": lambda x, b1, b2: b1 * b2 * x * (1 + b2 * x) ** (-1),
    "Roszman1": lambda x, b1, b2, b3, b4: b1 - b2 * x - np.arctan(b3 / (x - b4)) / pi,
    "ENSO": lambda x, b1, b2, b3, b4, b5, b6, b7, b8, b9: b1 + b2 * np.cos(2 * pi * x / 12) + b3 * np.sin(2 * pi * x / 12) + b5 * np.cos(2 * pi * x / b4) + b6 * np.sin(2 * pi * x / b4) + b8 * np.cos(2 * pi * x / b7) + b9 * np.sin(2 * pi * x / b7),
    "MGH09": lambda x, b1, b2, b3, b4: b1 * (x ** 2 + x * b2) / (x ** 2 + x * b3 + b4),
    "Thurber": lambda x, b1, b2, b3, b4, b5, b6, b7: (b1 + b2 * x + b3 * x ** 2 + b4 * x ** 3) / (1 + b5 * x + b6 * x ** 2 + b7 * x ** 3),
    "BoxBOD": lambda x, b1, b2: b1 * (1 - e(-b2 * x)),
    "Rat42": lambda x, b1, b2, b3: b1 / (1 + e(b2 - b3 * x)),
    "MGH10": lambda x, b1, b2, b3: b1 * e(b2 / (x + b3)),
    "Eckerle4": lambda x, b1, b2, b3: (b1 / b2) * e(-0.5 * ((x - b3) / b2) ** 2),
    "Rat43": lambda x, b1, b2, b3, b4: b1 / (1 + e(b2 - b3 * x)) ** (1 / b4),
    "Bennett5": lambda x, b1, b2, b3: b1 * (b2 + x) ** (-1 / b3),
}
HIGHER = {"MGH09", "Thurber", "BoxBOD", "Rat42", "MGH10", "Eckerle4", "Rat43", "Bennett5"}


def parse(path):
    lines = open(path, encoding="latin-1").read().splitlines()
    start1, start2, cert, sd = [], [], [], []
    rss = None
    data_at = None
    for i, l in enumerate(lines):
        t = l.split()
        if len(t) >= 6 and t[0].startswith("b") and t[1] == "=":
            start1.append(float(t[2])); start2.append(float(t[3]))
            cert.append(float(t[4])); sd.append(float(t[5]))
        if l.strip().startswith("Residual Sum of Squares"):
            rss = float(t[-1])
        if l.startswith("Data:") and ("y" in t[1:] and "x" in "".join(t[1:])):
            data_at = i + 1
    rows = [list(map(float, l.split())) for l in lines[data_at:] if l.strip()]
    arr = np.array(rows)
    return {"start1": start1, "start2": start2, "cert": cert, "sd": sd, "rss": rss, "data": arr}
