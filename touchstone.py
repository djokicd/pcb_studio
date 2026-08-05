"""Touchstone (.sNp) parsing and S-parameter network folding.

Used for EM/circuit co-simulation: the FDTD extracts the board's full
N-port S-matrix (one excitation run per port), and the devices described
by Touchstone files are connected to their board ports in circuit space:

    b_c = Sce a_e + Scc a_c,   a_c = Sd b_c
    =>  S_ext = See + Sec Sd (I - Scc Sd)^-1 Sce

All matrices are lists of lists of complex; no numpy required.
"""
import cmath
import math
import re


class TouchstoneError(Exception):
    pass


_UNIT = {'HZ': 1.0, 'KHZ': 1e3, 'MHZ': 1e6, 'GHZ': 1e9}


def parse_touchstone(text, nports=None):
    """Parse a Touchstone v1 file.

    Returns {'nports', 'r': ref impedance, 'freq': [Hz], 's': [matrix per
    freq]} with s[k][i][j] = S(i+1)(j+1). The 2-port column order quirk
    (S11 S21 S12 S22) is handled.
    """
    unit = 1e9
    fmt = 'MA'
    r = 50.0
    numbers = []
    for raw in text.splitlines():
        line = raw.split('!', 1)[0].strip()
        if not line:
            continue
        if line.startswith('#'):
            toks = line[1:].upper().split()
            i = 0
            while i < len(toks):
                t = toks[i]
                if t in _UNIT:
                    unit = _UNIT[t]
                elif t == 'S':
                    pass
                elif t in ('MA', 'DB', 'RI'):
                    fmt = t
                elif t == 'R' and i + 1 < len(toks):
                    r = float(toks[i + 1])
                    i += 1
                elif t in ('Y', 'Z', 'H', 'G'):
                    raise TouchstoneError(f'only S-parameter files supported (got {t})')
                i += 1
            continue
        if line.startswith('['):
            raise TouchstoneError('Touchstone v2 files are not supported')
        try:
            numbers.extend(float(v) for v in line.split())
        except ValueError:
            raise TouchstoneError(f'bad data line: {line[:60]}')
    if not numbers:
        raise TouchstoneError('no data found')

    # infer port count if not given: the stride must divide the data AND
    # yield a strictly increasing frequency column
    if nports is None:
        for n in (1, 2, 3, 4, 6, 8):
            per_n = 1 + 2 * n * n
            if len(numbers) % per_n:
                continue
            fcol = numbers[0::per_n]
            if all(b > a for a, b in zip(fcol, fcol[1:])):
                nports = n
                break
        else:
            raise TouchstoneError('cannot infer port count')
    per = 1 + 2 * nports * nports
    if len(numbers) % per:
        raise TouchstoneError(
            f'data size {len(numbers)} does not match {nports} ports')

    def val(a, b):
        if fmt == 'RI':
            return complex(a, b)
        if fmt == 'MA':
            return cmath.rect(a, math.radians(b))
        return cmath.rect(10 ** (a / 20.0), math.radians(b))   # DB

    freq = []
    mats = []
    for k in range(len(numbers) // per):
        chunk = numbers[k * per:(k + 1) * per]
        freq.append(chunk[0] * unit)
        vals = [val(chunk[1 + 2 * m], chunk[2 + 2 * m])
                for m in range(nports * nports)]
        m = [[0j] * nports for _ in range(nports)]
        idx = 0
        for i in range(nports):
            for j in range(nports):
                if nports == 2:
                    # 2-port files are column-major: S11 S21 S12 S22
                    m[j][i] = vals[idx]
                else:
                    m[i][j] = vals[idx]
                idx += 1
        mats.append(m)
    if any(b <= a for a, b in zip(freq, freq[1:])):
        raise TouchstoneError('frequencies are not strictly increasing')
    return {'nports': nports, 'r': r, 'freq': freq, 's': mats}


def interpolate(ts, freq_hz):
    """Device S-matrices linearly interpolated onto `freq_hz`.
    Returns (mats, clamped: bool)."""
    f = ts['freq']
    out = []
    clamped = False
    for target in freq_hz:
        if target <= f[0]:
            out.append(ts['s'][0])
            clamped = clamped or target < f[0] * 0.999
            continue
        if target >= f[-1]:
            out.append(ts['s'][-1])
            clamped = clamped or target > f[-1] * 1.001
            continue
        hi = next(i for i, v in enumerate(f) if v >= target)
        lo = hi - 1
        w = (target - f[lo]) / (f[hi] - f[lo])
        n = ts['nports']
        out.append([[ts['s'][lo][i][j] * (1 - w) + ts['s'][hi][i][j] * w
                     for j in range(n)] for i in range(n)])
    return out, clamped


# ------------------------------------------------------------ linear algebra
def _solve(A, B):
    """Solve A X = B for complex matrices (Gaussian elimination, partial
    pivoting). A is n x n, B is n x m; returns X."""
    n = len(A)
    m = len(B[0])
    M = [[A[i][j] for j in range(n)] + [B[i][j] for j in range(m)]
         for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-30:
            raise TouchstoneError('singular matrix while folding network')
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                f = M[r][col]
                M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [row[n:] for row in M]


def _matmul(A, B):
    n, k, m = len(A), len(B), len(B[0])
    return [[sum(A[i][p] * B[p][j] for p in range(k)) for j in range(m)]
            for i in range(n)]


def connect(board, dev, pins):
    """Fold a device into a board S-matrix (same reference impedance).

    board: N x N matrix; dev: K x K matrix; pins: list of K board port
    INDICES (0-based) connected to device ports 1..K in order.
    Returns the (N-K) x (N-K) matrix over the remaining ports (original
    relative order preserved).
    """
    n = len(board)
    k = len(dev)
    ext = [i for i in range(n) if i not in pins]
    See = [[board[a][b] for b in ext] for a in ext]
    Sec = [[board[a][pins[j]] for j in range(k)] for a in ext]
    Sce = [[board[pins[i]][b] for b in ext] for i in range(k)]
    Scc = [[board[pins[i]][pins[j]] for j in range(k)] for i in range(k)]
    # X = (I - Scc Sd)^-1 Sce
    SccSd = _matmul(Scc, dev)
    I_minus = [[(1 if i == j else 0) - SccSd[i][j] for j in range(k)]
               for i in range(k)]
    X = _solve(I_minus, Sce)
    add = _matmul(Sec, _matmul(dev, X))
    return [[See[i][j] + add[i][j] for j in range(len(ext))]
            for i in range(len(ext))]
