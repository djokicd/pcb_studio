# OpenEMS PCB Studio

A browser-based GUI for setting up and running [openEMS](https://openems.de)
FDTD simulations of planar PCB structures via GNU Octave.

## Features

- **2D canvas editor** (top view, mm units) with pictogram tools:
  rectangle, circle, circle segment (pie sector), arc (curved trace),
  polygon (click points, Enter/double-click closes), via, discrete
  component and lumped port. Objects can be moved, resized via drag
  handles (rects, circle radius, polygon vertices) or edited numerically.
  Grid snapping, zoom/pan, keyboard shortcuts.
- **Stackup editor**: arbitrary layer stack (top→bottom) of conductor and
  dielectric layers — thickness, εr, tan δ per dielectric, optional full
  copper plane per conductor. Shapes/ports/components reference conductor
  layers; conductors are modelled as zero-thickness PEC sheets at their
  stackup interface.
- **Vias**: drill/pad diameter, connecting any two conductor layers
  (metal barrel + pads on every crossed conductor layer).
- **Discrete components**: R / L / C lumped elements in 0402, 0603, 0805
  or custom footprints, 0°/90° rotation, values in Ω / pF / nH
  (openEMS `AddLumpedElement` with end caps).
- **Lumped ports**: z-directed between two conductor layers or in-plane
  (x/y) sheet ports; per-port impedance; exactly one excited port.
- **Mesh preview**: toggleable overlay of the exact simulation mesh with
  live cell count, plus a stackup cross-section strip showing the z mesh
  lines, dielectric slabs and conductor sheets. The mesh is computed in
  Python and emitted into the script as explicit line vectors, so
  preview == simulation. The background mm grid can be toggled off (G).
- **Run from the GUI**: Octave/openEMS subprocess with a graphical run
  monitor — progress bar with ETA, stat tiles (timestep, speed, energy,
  cells), a live energy-decay chart with the end-criteria target line
  and a solver-speed chart. All solver output is parsed: engine facts
  (version, engine type, threads, FDTD size, timestep, Nyquist rate,
  excitation length, final speed) appear in an Engine table and every
  solver warning is surfaced in a highlighted box; non-converged runs
  are labelled as such. The raw terminal log stays collapsed by
  default (auto-expands on errors). Runs can be stopped.
- **Server-side projects**: Save stores the project on the server
  (`projects/<name>/project.json`) together with a copy of the latest
  run's results (S-parameters, port signals, current-density exports —
  bulky raw field dumps excluded); Open lists the stored projects
  (with a "results" badge, delete supported) and restores both the
  design and its results. "Export cfg" downloads the project JSON,
  and the Open dialog can import one.
- **Results** open in a dedicated Results tab in the main area (the GUI
  switches to it when a run finishes), all interactive (hover tooltips)
  and pop-out-able to a large modal:
  - Reflection: magnitude (dB) plot or **Smith chart** (Γ and denormalised
    impedance readout on hover)
  - Transmission: magnitude (dB) plot or **polar plot** (|S| dB vs phase)
  - **Time domain**: the raw port signals of every port (lumped and
    MSL — for MSL ports the measurement-plane probes are used). Any
    combination of voltages and currents plots on one shared axis:
    currents are drawn as i(t)·Z₀ so they are directly comparable with
    the voltages (equal traces = matched wave); tooltips report the
    raw current in amperes. Legend chips toggle each signal
    (voltages shown by default, currents hidden).
  - **Recorded J(t)**: time-domain rot(H) current density captured
    during a configurable window (StartTime/StopTime, optional spatial
    subsampling), decimated to ~160 frames and played back like the
    port signals — switchable against the single-frequency phasor view.
  - **Current density over time**: frequency-domain rot(H) dumps per
    conductor layer, animated as Re{J·e^{jφ}} over one RF period with
    play/pause and a phase slider; layer and frequency selectable. Dump
    frequencies accept a GHz list or `all` (21 points across the sweep),
    and any *arbitrary* in-band frequency can be viewed — the complex
    field is linearly interpolated between the two nearest dumped
    points (marked "interpolated"). The animation exports as a looping
    GIF (dependency-free encoder, one RF period in phasor mode or the
    recorded frames in J(t) mode).
    The non-uniform FDTD grid is resampled with bilinear interpolation
    in physical coordinates (toggleable — off shows the raw mesh cells),
    with a color-scale bar.

  With three or more ports, the legend chips on the Transmission and
  Time-domain cards toggle individual traces on/off (persisted).
  Result cards can be rearranged by dragging their ☰ grip and resized
  in both directions via the resize corner; the Results pane is further
  configurable with per-card show/hide toggles, a column-count selector
  and a "Reset layout" button (layout persisted per browser). Cartesian charts zoom
  by drag-selecting a range or with the mouse wheel (double-click
  resets); Smith/polar charts and the current-density map zoom with
  the wheel and pan by dragging. Every chart exports to PNG, and the
  S-parameter/time-domain charts also export CSV.
- **Per-object meshing**: every shape/port/via/component takes an
  optional local mesh resolution; shapes additionally support the
  metal-edge 1/3–2/3 refinement rule (offset capped by the feature
  size). Near-coincident mesh lines are merged (sim setting "Merge
  lines <", default 0.1 mm) to protect the FDTD timestep.
- **Discrete R/L/C accuracy**: lumped-element boxes are automatically
  shrunk to the copper-free gap they bridge, so the nominal value
  applies across the gap regardless of how the body overlaps the pads;
  unconnected component ends are flagged as design-rule warnings.
- **Fabrication data import**: per-layer Gerber (RS-274X subset:
  standard apertures, strokes, arcs, regions) via the ⇪ button on a
  stackup conductor layer, and Excellon drill files creating PTH vias.
  A shared import offset keeps all imported files aligned; the board
  outline grows to fit. Imported geometry meshes by bounding box to
  keep cell counts sane.
- **MSL ports**: matched microstrip-line ports (openEMS `AddMSLPort`)
  with de-embedded reference planes, available as their own toolbar
  tool. Drag one at a line end; the chevrons point into the board
  (auto-set toward the nearest edge, editable). The generator extends
  the strip, substrate and ground plane to the absorbing boundary so
  the port launches into the true line cross-section, places the feed
  in the launch run and the S-parameter reference plane at the port's
  inner edge. Use these instead of lumped ports when accurate line
  impedance / return loss matters (a 50 Ω test line measures S11
  < −30 dB with MSL ports vs ≈ −25 dB with lumped ports). Requires an
  absorbing boundary (MUR/PML-8); S-parameters are referenced to the
  port's "Ref. impedance".
- **Design rule warnings**: objects outside the board outline and
  z-ports that don't land on copper are flagged in the editor and
  before each run.
- **Built-in verification tests**: the Tests tab in the main area runs
  the test suite from the GUI — fast code-level checks (parsers, mesh,
  script generation, API) and four benchmark simulations of canonical
  structures (series resistor, microstrip Z₀/εeff, quarter-wave stub
  notch, patch resonance), each showing a reference-vs-obtained
  comparison table with acceptance windows. The same cases run headless
  via `python3 -m pytest -m sim` (see `tests/README.md`).
- **Editor conveniences**: Ctrl+C/X/V copy, cut and paste the selected
  object (repeated pastes cascade; Ctrl+D duplicates), the right panel
  is resizable via its drag handle, and all confirmations and notices
  use in-app dialogs/toasts (native browser dialogs are suppressed in
  some embedded browsers).
  - Raw `sparams.csv` (complex S and Zin) download.
- **Export** the self-contained Octave `.m` script; save/load projects as
  JSON (auto-saved to browser localStorage, old single-substrate projects
  are migrated automatically).

## Requirements

- Python 3 with Flask
- GNU Octave
- openEMS with its Octave/Matlab interface. Looked for under
  `$OPENEMS_MATLAB_ROOT`, `~/opt/openEMS/share`, `/usr/local/share`,
  `/usr/share` (first match wins).

## Run

```bash
python3 server.py
```

Then open <http://localhost:8036> (set `PORT` to change).

## Layout

- `server.py` – Flask app: static UI, `/api/script`, `/api/run`,
  `/api/status`, `/api/stop`, `/api/mesh` (preview),
  `/api/results/<run>/…` (sparams.csv, jdumps list, per-layer current
  density binaries)
- `scriptgen.py` – model validation + Octave script generation
- `geometry.py` – stackup z-positions, shape outlines, footprints
- `meshlines.py` – mesh line generation (shared by preview and script)
- `static/` – vanilla JS UI: `js/editor.js` (canvas editor),
  `js/charts.js` (dB/Smith/polar + modal), `js/jview.js` (current
  density animation), `js/app.js` (state, panels, run control)
- `sims/run_*/` – one working directory per run

## Conventions

- Geometry in **mm**, board lower-left corner is the origin, y up,
  z = 0 at the bottom of the stackup.
- Curved shapes are polygonised (64 segments per full circle) both for
  the simulation (`AddLinPoly`) and mesh-line extraction.
- Current density dumps are frequency-domain (`DumpType 13`, HDF5) at
  user-selected frequencies (default: sweep centre f0); the generated
  script converts them to flat `J_<layer>_f<k>.bin` files via
  `ReadHDF5Dump` so the GUI needs no HDF5 dependency.
- Progress = max(timesteps / max. timesteps, energy dB / end criteria).
