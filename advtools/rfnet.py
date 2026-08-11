"""Minimal microwave-network layer: the slice of scikit-rf that `rfamp`
actually uses, implemented on numpy.

The RF chain tool arrived as a standalone scikit-rf application. Pulling
scikit-rf (and its dependency tree) into a project that deliberately runs
on Flask alone would be a poor trade for the handful of textbook formulas
involved: a two-port container, the S/Z/ABCD conversions, a cascade, and
an ideal-line medium. All of that is a few hundred lines of numpy, so it
lives here and `rfamp` imports this module in scikit-rf's place.

The API deliberately mirrors scikit-rf's names and shapes, so the upstream
package's own test suite runs against it unchanged - which is how the port
is verified.

S-parameter arrays are (nfreq, n, n); z0 is a real scalar per network.
"""
import numpy as np

C0 = 299792458.0

_UNIT = {'hz': 1.0, 'khz': 1e3, 'mhz': 1e6, 'ghz': 1e9, 'thz': 1e12}


class Frequency:
    """A frequency axis. Constructed as (start, stop, npoints, unit) or
    from an explicit array via `from_f`."""

    def __init__(self, start=None, stop=None, npoints=None, unit='hz', f=None):
        if f is not None:
            self.f = np.asarray(f, dtype=float)
        else:
            scale = _UNIT[str(unit).lower()]
            self.f = np.linspace(float(start), float(stop), int(npoints)) * scale
        self.unit = 'hz'

    @classmethod
    def from_f(cls, f, unit='hz'):
        scale = _UNIT[str(unit).lower()]
        return cls(f=np.asarray(f, dtype=float) * scale)

    @property
    def npoints(self):
        return len(self.f)

    @property
    def w(self):
        return 2.0 * np.pi * self.f

    def __len__(self):
        return len(self.f)

    def __getitem__(self, k):
        return Frequency(f=self.f[k])

    def __repr__(self):
        if not len(self.f):
            return 'Frequency(empty)'
        return (f'Frequency({self.f[0] / 1e9:g}-{self.f[-1] / 1e9:g} GHz, '
                f'{len(self.f)} points)')


# ---------------------------------------------------------------- conversions
# Standard power-wave relations for a real reference impedance.

def s2z(s, z0=50.0):
    s = np.asarray(s, dtype=complex)
    n = s.shape[-1]
    ident = np.eye(n, dtype=complex)
    z0 = float(np.real(np.mean(z0)))
    return z0 * np.matmul(ident + s, np.linalg.inv(ident - s))


def z2s(z, z0=50.0):
    z = np.asarray(z, dtype=complex)
    n = z.shape[-1]
    ident = np.eye(n, dtype=complex)
    z0 = float(np.real(np.mean(z0)))
    return np.matmul(z - z0 * ident, np.linalg.inv(z + z0 * ident))


def s2a(s, z0=50.0):
    """Two-port S -> ABCD."""
    s = np.asarray(s, dtype=complex)
    z0 = float(np.real(np.mean(z0)))
    s11, s12 = s[..., 0, 0], s[..., 0, 1]
    s21, s22 = s[..., 1, 0], s[..., 1, 1]
    den = 2.0 * s21
    a = ((1 + s11) * (1 - s22) + s12 * s21) / den
    b = z0 * ((1 + s11) * (1 + s22) - s12 * s21) / den
    c = ((1 - s11) * (1 - s22) - s12 * s21) / (den * z0)
    d = ((1 - s11) * (1 + s22) + s12 * s21) / den
    out = np.empty(s.shape, dtype=complex)
    out[..., 0, 0], out[..., 0, 1] = a, b
    out[..., 1, 0], out[..., 1, 1] = c, d
    return out


def a2s(a, z0=50.0):
    """Two-port ABCD -> S."""
    a = np.asarray(a, dtype=complex)
    z0 = float(np.real(np.mean(z0)))
    A, B = a[..., 0, 0], a[..., 0, 1]
    C, D = a[..., 1, 0], a[..., 1, 1]
    den = A + B / z0 + C * z0 + D
    s11 = (A + B / z0 - C * z0 - D) / den
    s12 = 2.0 * (A * D - B * C) / den
    s21 = 2.0 / den
    s22 = (-A + B / z0 - C * z0 + D) / den
    out = np.empty(a.shape, dtype=complex)
    out[..., 0, 0], out[..., 0, 1] = s11, s12
    out[..., 1, 0], out[..., 1, 1] = s21, s22
    return out


# a `network` submodule alias, so `rfnet.network.s2z(...)` reads like the
# scikit-rf call it replaces
class _NetworkFuncs:
    s2z = staticmethod(s2z)
    z2s = staticmethod(z2s)
    s2a = staticmethod(s2a)
    a2s = staticmethod(a2s)


network = _NetworkFuncs()


class Network:
    """An n-port S-parameter network on a frequency axis.

    Network(frequency=..., s=..., z0=...) | Network(path) | Network(other)
    """

    def __init__(self, file=None, frequency=None, s=None, z0=50.0, name=None):
        if isinstance(file, Network):
            other = file
            self.frequency = Frequency(f=other.frequency.f.copy())
            self.s = other.s.copy()
            self._z0 = other._z0
            self.name = name or other.name
            return
        if isinstance(file, str):
            freq, sarr, ref = _read_touchstone(file)
            self.frequency = Frequency(f=freq)
            self.s = sarr
            self._z0 = ref
            self.name = name or file.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            return
        if frequency is None or s is None:
            raise ValueError('Network needs a file, or frequency and s')
        self.frequency = (frequency if isinstance(frequency, Frequency)
                          else Frequency(f=np.asarray(frequency, dtype=float)))
        self.s = np.asarray(s, dtype=complex)
        self._z0 = float(np.real(np.mean(z0)))
        self.name = name

    # -- basics --------------------------------------------------------
    @property
    def f(self):
        return self.frequency.f

    @property
    def nports(self):
        return self.s.shape[-1]

    @property
    def z0(self):
        # scikit-rf exposes a per-port, per-frequency array; callers here
        # only ever read a single element or take the real part
        return np.full((len(self.f), self.nports), self._z0, dtype=complex)

    def copy(self):
        return Network(self)

    def renormalize(self, z_new):
        """Re-reference the S-parameters to another impedance, in place.

        Uses the power-wave form S' = (S - rI)(I - rS)^-1 rather than a
        detour through Z: a half-wave line has S = [[0,-1],[-1,0]], for
        which I - S is singular and the Z matrix does not exist, so the
        Z route turns a perfectly passive line into a 1.2x amplifier at
        exactly those frequencies. With |r| < 1 for positive impedances
        and |eig(S)| <= 1 for a passive network, I - rS is always
        invertible.
        """
        z_new = float(np.real(np.mean(z_new)))
        if abs(z_new - self._z0) < 1e-12:
            return
        r = (z_new - self._z0) / (z_new + self._z0)
        ident = np.eye(self.nports, dtype=complex)
        self.s = np.matmul(self.s - r * ident,
                           np.linalg.inv(ident - r * self.s))
        self._z0 = z_new

    @property
    def stability(self):
        """Rollett's K, vs frequency (two-port) - scikit-rf's name."""
        s = self.s
        s11, s12 = s[:, 0, 0], s[:, 0, 1]
        s21, s22 = s[:, 1, 0], s[:, 1, 1]
        delta = s11 * s22 - s12 * s21
        return ((1 - np.abs(s11) ** 2 - np.abs(s22) ** 2 + np.abs(delta) ** 2)
                / (2 * np.abs(s12 * s21)))

    def flipped(self):
        """Ports swapped (two-port)."""
        s = self.s.copy()
        s[:, [0, 1], :] = s[:, [1, 0], :]
        s[:, :, [0, 1]] = s[:, :, [1, 0]]
        return Network(frequency=self.frequency, s=s, z0=self._z0,
                       name=self.name)

    def __len__(self):
        return len(self.f)

    def __repr__(self):
        return (f'Network({self.name or "unnamed"}, {self.nports}-port, '
                f'{len(self.f)} points)')

    # -- cascade -------------------------------------------------------
    def __pow__(self, other):
        """Cascade two two-ports (scikit-rf's ** operator)."""
        if self.nports != 2 or other.nports != 2:
            raise ValueError('cascade needs two two-ports')
        if len(self.f) != len(other.f) or not np.allclose(self.f, other.f):
            raise ValueError('cascaded networks need the same frequency axis')
        a = np.matmul(s2a(self.s, self._z0), s2a(other.s, other._z0))
        return Network(frequency=self.frequency, s=a2s(a, self._z0),
                       z0=self._z0)

    # -- interpolation --------------------------------------------------
    def interpolate(self, new_frequency, kind='cubic', **_kw):
        """Resample onto another frequency axis (never extrapolates)."""
        target = (new_frequency.f if isinstance(new_frequency, Frequency)
                  else np.asarray(new_frequency, dtype=float))
        f = self.f
        if target.min() < f.min() - 1e-6 or target.max() > f.max() + 1e-6:
            raise ValueError('interpolation would extrapolate beyond the '
                             'measured frequency range')
        out = np.empty((len(target),) + self.s.shape[1:], dtype=complex)
        for i in range(self.nports):
            for j in range(self.nports):
                out[:, i, j] = (_interp1d(f, self.s[:, i, j].real, target, kind)
                                + 1j * _interp1d(f, self.s[:, i, j].imag,
                                                 target, kind))
        return Network(frequency=Frequency(f=target), s=out, z0=self._z0,
                       name=self.name)


def _interp1d(x, y, xi, kind='cubic'):
    if kind == 'cubic' and len(x) >= 4:
        try:
            from scipy.interpolate import CubicSpline
            return CubicSpline(x, y)(xi)
        except Exception:
            pass
    return np.interp(xi, x, y)


def _read_touchstone(path):
    """Read a Touchstone file through the project's own parser, so the
    tool sees exactly the files the rest of the GUI does."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from touchstone import parse_touchstone
    with open(path, 'r', errors='replace') as fh:
        ts = parse_touchstone(fh.read())
    freq = np.asarray(ts['freq'], dtype=float)
    n = int(ts['nports'])
    s = np.asarray(ts['s'], dtype=complex).reshape(len(freq), n, n)
    return freq, s, float(ts.get('r') or 50.0)


# -------------------------------------------------------------------- media
class DefinedGammaZ0:
    """Ideal, lossless, dispersion-free transmission-line medium plus the
    lumped elements built on it (scikit-rf's `media` interface subset)."""

    def __init__(self, frequency, z0=50.0, gamma=None, **_kw):
        self.frequency = (frequency if isinstance(frequency, Frequency)
                          else Frequency(f=np.asarray(frequency, dtype=float)))
        self.z0 = float(np.real(z0))
        self._gamma = gamma

    # -- helpers -------------------------------------------------------
    @property
    def _w(self):
        return 2.0 * np.pi * self.frequency.f

    def _beta(self):
        return self._w / C0

    def _from_abcd(self, a):
        return Network(frequency=self.frequency, s=a2s(a, self.z0), z0=self.z0)

    def _abcd(self, A, B, C, D):
        n = len(self.frequency.f)
        out = np.empty((n, 2, 2), dtype=complex)
        one = np.ones(n, dtype=complex)
        out[:, 0, 0] = A * one
        out[:, 0, 1] = B * one
        out[:, 1, 0] = C * one
        out[:, 1, 1] = D * one
        return out

    def _series(self, z):
        return self._from_abcd(self._abcd(1.0, z, 0.0, 1.0))

    def _shunt(self, y):
        return self._from_abcd(self._abcd(1.0, 0.0, y, 1.0))

    def _length_m(self, length, unit='m'):
        u = str(unit).lower()
        if u in ('m', 'meter', 'meters'):
            return float(length)
        if u in ('mm',):
            return float(length) * 1e-3
        if u in ('cm',):
            return float(length) * 1e-2
        if u in ('deg', 'degree', 'degrees'):
            # electrical degrees at each frequency -> handled by line()
            return float(length)
        raise ValueError(f'unsupported length unit {unit!r}')

    # -- elements ------------------------------------------------------
    def line(self, length, unit='m', z0=None, **_kw):
        """Ideal line of the given physical length (vp = c)."""
        zc = self.z0 if z0 is None else float(np.real(z0))
        u = str(unit).lower()
        if u in ('deg', 'degree', 'degrees'):
            theta = np.full(len(self.frequency.f),
                            np.radians(float(length)), dtype=complex)
        else:
            theta = self._beta() * self._length_m(length, unit)
        cos, sin = np.cos(theta), np.sin(theta)
        n = len(self.frequency.f)
        a = np.empty((n, 2, 2), dtype=complex)
        a[:, 0, 0] = cos
        a[:, 0, 1] = 1j * zc * sin
        a[:, 1, 0] = 1j * sin / zc
        a[:, 1, 1] = cos
        return Network(frequency=self.frequency, s=a2s(a, self.z0), z0=self.z0)

    def inductor(self, L, **_kw):
        return self._series(1j * self._w * float(L))

    def capacitor(self, C, **_kw):
        return self._series(1.0 / (1j * self._w * float(C)))

    def resistor(self, R, **_kw):
        return self._series(np.full(len(self._w), float(R), dtype=complex))

    def shunt_inductor(self, L, **_kw):
        return self._shunt(1.0 / (1j * self._w * float(L)))

    def shunt_capacitor(self, C, **_kw):
        return self._shunt(1j * self._w * float(C))

    def shunt_resistor(self, R, **_kw):
        return self._shunt(np.full(len(self._w), 1.0 / float(R), dtype=complex))

    def _stub_admittance(self, length, unit, open_circuit):
        u = str(unit).lower()
        if u in ('deg', 'degree', 'degrees'):
            theta = np.full(len(self.frequency.f),
                            np.radians(float(length)), dtype=complex)
        else:
            theta = self._beta() * self._length_m(length, unit)
        t = np.tan(theta)
        # open stub: Y = j*tan(bl)/Z0 ; short stub: Y = -j*cot(bl)/Z0
        with np.errstate(divide='ignore', invalid='ignore'):
            y = (1j * t / self.z0) if open_circuit else (-1j / (t * self.z0))
        return np.where(np.isfinite(y), y, 0.0 + 0.0j)

    def shunt_delay_open(self, length, unit='m', **_kw):
        return self._shunt(self._stub_admittance(length, unit, True))

    def shunt_delay_short(self, length, unit='m', **_kw):
        return self._shunt(self._stub_admittance(length, unit, False))


class media:            # noqa: N801 - mirrors `skrf.media` as a namespace
    DefinedGammaZ0 = DefinedGammaZ0
