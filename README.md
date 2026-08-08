# OpenEMS PCB Studio

A browser-based GUI for setting up and running [openEMS](https://openems.de)
FDTD simulations of planar PCB structures via GNU Octave.

## Features

- **2D canvas editor** (top view, mm units) with pictogram tools:
  rectangle, circle, circle segment (pie sector), arc (curved trace),
  polygon (click points, Enter/double-click closes), via, discrete
  component, lumped port and a **measure tool** (X — drag between two
  points to read Δx, Δy and the distance; Esc clears). Objects can be
  moved, resized via drag handles (rects, circle radius, polygon
  vertices) or edited numerically. Zoom/pan, keyboard shortcuts.
- **Transmission-line tool** (T): click centerline points
  (Enter/double-click finishes) to draw a line of configurable width;
  interior corners are optionally rounded with a configurable radius
  (sampled fillet), so curved routes and meanders are easy. Hovering a
  line shows the cumulative electrical length from its start to the
  cursor plus the total centerline length; the properties panel edits
  width, corner radius and shows the length. The stroked outline
  (mitered sides, round caps) is generated identically in the editor
  and in the Python geometry, so the mesh and simulation see exactly
  the drawn line.
- **Canvas notes** (N): collapsible plain-text annotations pinned to a
  board position. Each note has its own **title** (always visible, also
  when collapsed) and body text, folded away by clicking the note's
  triangle or double-clicking it, plus a per-note **marker colour**
  (pin, border and triangle) chosen from quick swatches or a colour
  picker — notes left on the default colour follow the light/dark
  theme. Notes are drawn at a fixed size so they stay readable at any
  zoom, are edited in the properties panel with live preview,
  move/copy/delete like any other object, and are saved with the
  project — but they are documentation only and never reach the mesh or
  the generated Octave script.
- **Menu bar** (File / Edit / View / Tools / Help) with the usual
  project, clipboard and view commands, keyboard-shortcut hints and a
  shortcuts reference under Help. The Tools menu is grouped into
  Editing / Drawing / Placement / Configuration / Verification, and the
  Tests view opens from Tools → Verification tests… (it no longer
  occupies a permanent main-area tab).
- **Snapping**: arbitrary snap-grid resolution (any mm value, 0 = off),
  restrictable to x-only or y-only grid ticks, plus optional snapping
  to corners/vertices of existing objects. The background grid always
  shows the configured snap grid (coarsened by 2/5/10× when zoomed out).
- **Loaded-for-review pane**: the left sidebar switches between Design
  (tools + objects) and **Loaded** — projects held for review
  *without* opening them in the editor. Click one to show its latest
  results, tick "cmp" to overlay its S-parameters on the current
  results (resampled onto the current sweep; overlay traces get their
  own legend chips), ▤ to browse its runs, ✕ to drop it. The project
  open in the editor heads the list. Add projects with ＋ add, or with
  ⊕ in the Open dialog. The list is remembered per browser.
- **Project browser with folders** (File → Open): the complete tree of
  stored projects, organized into **named folders with optional tags,
  nested to any depth**. Drag a project onto a folder (or a folder onto
  a folder) to move it; drop on empty space for the top level. Folder
  rows expand/collapse (state remembered) and offer ＋ subfolder,
  ✎ rename, 🏷 tags and ✕ delete — deleting a folder keeps its
  contents, moving them up one level, and moving a folder into its own
  subtree is rejected. The hierarchy is a pure organizational overlay
  stored in `projects/folders.json`: the on-disk project directories
  stay flat, so run ids, stored results and the completion auto-attach
  are untouched by any reorganizing.
- **Save places the project in a folder**: the first Save and
  File → Save as… ask for the project name *and* its destination
  folder, with an optional "new subfolder" field that creates the
  folder on the spot inside the selected one. Re-saving an existing
  project preselects the folder it already lives in.
- **Both side panels are resizable** by dragging the splitter next to
  them; the widths are remembered per browser.
- **Multi-selection**: with the Select tool, dragging on empty canvas
  box-selects every object fully inside; the group moves/nudges/
  copies/deletes together (Ctrl+A selects everything). Esc in any
  other tool returns to Select.
- **Themes & colors**: dark (default) and light theme
  (View → Light theme) covering the whole UI including the canvas and
  charts, and a configurable palette (View → Colors…) for the
  conductor-layer, port, MSL-port and device-pin colors — both stored
  per browser.
- **Stackup editor**: arbitrary layer stack (top→bottom) of conductor and
  dielectric layers — thickness, εr, tan δ per dielectric, optional full
  copper plane per conductor. Shapes/ports/components reference conductor
  layers; conductors are modelled as zero-thickness PEC sheets at their
  stackup interface.
- **Stackup manager** (Tools menu or the Manager… button in the Stackup
  tab): save the current stackup to a browser library, **edit any saved
  stackup's layers in place** (same layer editor as the Stackup tab),
  apply one to the project, export/import stackups as JSON files, and
  star one as the **default stackup for new projects** (used by
  File → New project). Drill files import via File → Import drill file;
  Gerbers via the ⇪ button on a conductor layer.
- **Vias**: drill/pad diameter, connecting any two conductor layers
  (metal barrel + pads on every crossed conductor layer).
- **Discrete components**: R / L / C lumped elements in 0402, 0603, 0805
  or custom footprints, 0°/90° rotation, values in Ω / pF / nH
  (openEMS `AddLumpedElement` with end caps).
- **Lumped ports**: z-directed between two conductor layers or in-plane
  (x/y) sheet ports; per-port impedance. **Any subset of ports can be
  excited**: with several excited ports the run launches one excitation
  per port (the lowest-numbered one carries the field dumps) and merges
  the resulting S-parameter columns into one result set, so e.g. S11,
  S21, S12 and S22 come from a single Run click without a full
  S-matrix sweep. The exported .m script excites the primary port only.
- **Mesh preview**: toggleable overlay of the exact simulation mesh with
  live cell count, plus a stackup cross-section strip showing the z mesh
  lines, dielectric slabs and conductor sheets. The mesh is computed in
  Python and emitted into the script as explicit line vectors, so
  preview == simulation. The background mm grid can be toggled off (G).
- **Run from the GUI**: Octave/openEMS subprocess with a graphical run
  monitor — progress bar with ETA, stat tiles (timestep, speed, energy,
  cells), a live energy-decay chart with the end-criteria target line
  and a solver-speed chart. Both charts autorange their x axis to the
  timesteps actually solved (labelled `timesteps (0 – N)`) rather than
  the configured limit. Multi-excitation runs get **one tab per
  excitation**: every solver run restarts at timestep 0, so their
  samples, engine facts and warnings are kept in separate records
  instead of being concatenated into overlapping curves. The tab of a
  stage is selected automatically as that stage starts, a manual
  selection is then left alone, and tabs mark stages that produced
  warnings or failed to converge. All solver output is parsed: engine facts
  (version, engine type, threads, FDTD size, timestep, Nyquist rate,
  excitation length, final speed) appear in an Engine table and every
  solver warning is surfaced in a highlighted box; non-converged runs
  are labelled as such. The raw terminal log stays collapsed by
  default (auto-expands on errors). Runs can be stopped.
- **Nothing is lost when the browser goes away.** Every run directory
  stores the exact model that produced it (`sims/run_*/project.json`),
  and the moment a run completes the server **automatically attaches
  its results to the project on disk** — no browser needed.
- **Per-project run history**: each completion is stored as its own
  `projects/<name>/runs/<run_id>/` — results *plus a snapshot of the
  editor state that produced them*, so several runs of the same project
  coexist. The ▤ button on a project in the Projects pane opens the
  project's **results browser**: every stored run with date and
  excitation count — click to open its results, ✎ to restore that
  run's exact design into the editor, ✕ to delete a stored run.
  (An older single `results/` copy still appears as a "legacy" entry.)
  The **Run tab in the right sidebar also lists the current project's
  past runs** (stored ones plus any matching runs still only under
  `sims/`, tagged "unsaved"), newest first with the open one
  highlighted — one click away from re-opening any previous result
  while watching a new run.
- **Gerber + drill export** (File → Export Gerber + drill): a zip with
  one RS-274X file per conductor layer (shape outlines as filled
  G36/G37 regions — exactly the polygons the simulation uses — via
  pads as circle flashes on the layers the via crosses, plane layers
  as a full-board region), a board-outline file, and an Excellon drill
  file for the vias. The generated files re-import cleanly through the
  tool's own Gerber/Excellon parsers. Note: plane regions carry no
  thermal reliefs or clearances. On page load the GUI restores the session in three
  layers: a run still solving re-attaches to the live monitor; a run
  that finished while the browser was away loads its results from the
  server's run state; otherwise a pointer kept in localStorage reloads
  the last results this browser had open (and the Results/Editor view
  you were on). **File → Browse runs…** lists every `sims/run_*` on
  disk — newest first, with project name and a results badge — to
  reopen any past run's results or reload the design that produced it
  (✎). For long unattended simulations, run the server as a systemd
  user service — `./scripts/install-service.sh`, see *Run as a service*
  below — so it outlives terminals and login sessions.
- **Server-side projects**: Save stores the project on the server
  (`projects/<name>/project.json`) together with a copy of the latest
  run's results (S-parameters, port signals, current-density exports —
  bulky raw field dumps excluded); Open lists the stored projects
  (with a "results" badge, delete supported) and restores both the
  design and its results. "Export cfg" downloads the project JSON,
  and the Open dialog can import one. The project name in the top bar
  is a read-only display: a project reads "Untitled project" until it
  is named by File → Save / Save as…, or by opening or importing one.
- **Results** open in a dedicated Results tab in the main area (the GUI
  switches to it when a run finishes), all interactive (hover tooltips)
  and pop-out-able to a large modal:
  - Reflection: magnitude (dB) plot, **Smith chart** (Γ and denormalised
    impedance readout on hover) or **VSWR** — the standing-wave ratio
    (1+|Γ|)/(1−|Γ|), floored at 1:1 with a soft 20:1 ceiling so one
    near-total reflection cannot flatten the rest; a port with |Γ| ≥ 1
    (active or unstable) reads ∞. The reflection CSV export carries a
    VSWR column alongside re/im/dB.
  - Transmission: magnitude (dB) plot or **polar plot** (|S| dB vs phase)
  - **Time domain**: the raw port signals of every port (lumped and
    MSL — for MSL ports the measurement-plane probes are used). Any
    combination of voltages and currents plots on one shared axis:
    currents are drawn as i(t)·Z₀ so they are directly comparable with
    the voltages (equal traces = matched wave); tooltips report the
    raw current in amperes. Legend chips toggle each signal
    (voltages shown by default, currents hidden).
  - **S-matrix (raw)**: for multi-excitation runs (the "Full S-matrix"
    option, a multi-port excitation selection, or any run with devices)
    the complete N×N matrix assembled from every excitation, shown as a
    clickable table at a selectable frequency — each entry as dB∠deg,
    mag∠deg or real/imag. Clicking a cell toggles that S_ij as a trace
    on the chart below, so any subset (a whole row, the diagonal, a
    reciprocity pair) can be compared directly, in **magnitude (dB), on
    a Smith chart, as VSWR or on a polar plot**; the table cells
    themselves also switch to VSWR. Smith and VSWR describe a
    reflection coefficient, so they use the diagonal entries S_ii only
    — transmission entries stay listed but are greyed out with a note
    (and show "—" in the VSWR table); the polar plot takes any entry. A
    selector switches
    between the **raw board matrix** (before device networks are folded
    in) and the **resulting network**, the whole matrix exports to CSV,
    and the untouched per-excitation `sparams.csv` of each FDTD run is
    linked for download. Handy as a sanity check: S_ij = S_ji on a
    reciprocal board.
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
    with a color-scale bar. In a multi-excitation simulation **every
    solver run records its own current density** — an excitation
    selector on the card switches between them — and the processed
    dumps ride along when the run is stored with its project, so the
    J viewer works for past runs too.
  - **Saved run diagnostics**: when a run finishes (or fails), the Run
    tab's diagnostic data — the per-excitation energy-decay and solver
    speed series, the engine facts table (timestep, FDTD size,
    excitation length, final speed…) and solver warnings — is written
    to `diagnostics.json` next to the results and stored with the run.
    Clicking any past run restores the plots and the table exactly as
    they looked live, with a note naming the run and its finish time.

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
- **Graded meshing**: inside every gap between geometry lines the cell
  sizes start from the fine detail at the gap ends and grow
  geometrically (sim setting **"Mesh grading ratio"**, default 1.5, the
  openEMS-recommended value) up to the local cap — so the fine mesh
  that captures transmission-line geometry relaxes smoothly into the
  bulk instead of jumping (abrupt size jumps cause spurious numerical
  reflections). The z axis is graded the same way and **asymmetric**:
  fine first cells at conductor faces that carry geometry (the strip
  side), coarser toward plane-only faces (the bulk ground) and through
  the air. The mesh preview status line reports the smallest cell
  (sets the timestep) and the **worst adjacent-cell step** — on a
  typical imported CPW board the rework took that step from ≈27× to
  ≈1.8×, and the microstrip benchmark's return-loss floor improved
  from −31 dB to −37 dB. **Curved and oblique copper edges** produce
  mesh lines that follow the actual edge: axis-aligned edges pin exact
  lines, curves are sampled along their length and thinned to the local
  resolution, so the staircase tracks the copper instead of its
  bounding box. **Curved transmission lines** (drawn traces and
  stroke chains merged by the simplify tool) additionally get per-axis
  cross-width fine bands along their whole run — a straight run
  refines across its width only, bends refine both axes — extending
  half a width past the copper edge so a coplanar-waveguide slot is
  resolved with the same fine cells. Via fences are resolved at pad
  scale (~3 cells across the pad), so 100+ stitching vias don't crush
  the cell budget; a per-via local resolution remains available for
  critical signal vias.
- **Per-object meshing**: every shape/port/via/component takes an
  optional local mesh resolution; shapes support the metal-edge
  1/3–2/3 refinement rule (offset capped by the feature size) as a
  tri-state: **auto** (the default) enables it for transmission-line
  features — traces and shapes narrower than 3 mm — and leaves pads,
  planes and Gerber imports coarse; on/off override per shape.
  Near-coincident mesh lines are merged (sim setting "Merge lines <",
  default 0.1 mm) to protect the FDTD timestep.
- **Discrete R/L/C accuracy**: lumped-element boxes are automatically
  shrunk to the copper-free gap they bridge, so the nominal value
  applies across the gap regardless of how the body overlaps the pads;
  unconnected component ends are flagged as design-rule warnings.
- **Fabrication data import**: per-layer Gerber (RS-274X subset:
  standard apertures, strokes, arcs, regions) via the ⇪ button on a
  stackup conductor layer, and Excellon drill files creating PTH vias.
  Stroked draws — including the arc tessellation — are chained into
  native **centerline traces** (one shape per drawn line, lightly
  decimated to 10 µm) instead of one rounded stroke polygon per
  segment, so imported boards arrive with editable widths and mesh
  cleanly from the start. The drill-import dialog asks how many **mesh
  lines each via** may pin per axis — auto (full round staircase),
  5 (centre + drill & pad edges), 3 (centre + drill edges) or
  1 (centre only) — because a fence of 100+ stitching vias meshed in
  full detail can dominate the cell budget (on the bundled CPW test
  board, drill-only import at 1 line/via meshes with ~21 % fewer
  cells *and* a larger timestep). The choice is remembered, applied to
  the imported vias, and editable per via afterwards (Properties →
  Meshing → Mesh lines), so critical signal vias can be set back to
  full detail individually.
- **Geometry simplification** (Tools → Simplify geometry…): rebuilds
  chains of previously imported stroke polygons into centerline
  traces (each stroke is validated by regenerating its outline before
  it may join a chain), thins densely tessellated polygon outlines
  with a Douglas–Peucker pass, and can resample curved lines to an
  even chord length. Copper edges move by at most the chosen
  tolerance (default 0.02 mm). The dialog previews shape/vertex
  counts and the mesh size before/after; applying is one undo step.
  On the bundled CPW test board this turns 236 stroke polygons into
  15 traces and cuts the shape list from 378 to 175.
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
- **S-parameter devices (Touchstone)**: black-box multiport devices —
  e.g. a transistor .s3p — placed in the circuit via EM/circuit
  co-simulation. Put a lumped port at each device pad, upload the .sNp
  to the server library (Simulation tab) and map device pins to port
  numbers. The run then performs one excitation per port to extract the
  full board S-matrix and folds the device networks in circuit space
  (S_ext = See + Sec·Sd·(I−Scc·Sd)⁻¹·Sce); the Results tab shows the
  combined external S-parameters, with the raw board matrix
  (`board_full.sNp`), the combined network (`combined.sNp`) and the
  device-less column (`sparams_board.csv`) downloadable. Device data is
  interpolated onto the sweep (clamped with a warning outside its
  range); pin ports must match the file's reference impedance. Device
  rows offer a 👁 preview of the file switchable between the |S| dB
  curves and a **Smith chart of the reflection coefficients** (S11,
  S22, …) — directly comparable against datasheet Smith figures. The
  file's leading `!` header comments are parsed and the **measurement /
  bias condition line is shown** in the device row and the preview
  title: vendor S-parameter sets ship one file per bias point (e.g.
  BFG25A at Vce=1V/Ic=0.1mA vs 1V/1mA), and using the wrong-bias file
  makes S11 look completely different from the datasheet curve while
  the tool is parsing it perfectly. Each pad can be named
  (gate/drain/source…) — pin ports are drawn violet in the editor with
  their `ref.pad` label. Full-matrix runs (automatic with devices,
  or opt-in via "Full S-matrix" in the Simulation tab) export every
  external port's own reflection, and the Reflection card then shows
  all S_ii together — legend chips toggle traces in both the dB view
  and the multi-trace Smith chart. Note this is circuit-level
  embedding — EM coupling between the device package and the board is
  not modelled, exactly as in standard EM/circuit co-simulation flows.
  A synthetic `fet_demo.s3p` ships in `devices/`.
- **Built-in verification tests**: the Tests tab in the main area runs
  the test suite from the GUI — fast code-level checks (parsers, mesh,
  script generation, API) and four benchmark simulations of canonical
  structures (series resistor, microstrip Z₀/εeff, quarter-wave stub
  notch, patch resonance), each showing a reference-vs-obtained
  comparison table with acceptance windows. The same cases run headless
  via `python3 -m pytest -m sim` (see `tests/README.md`).
- **Undo / redo** (Ctrl+Z / Ctrl+Y or Ctrl+Shift+Z, also in the Edit
  menu): full-project snapshot history (80 steps, deduplicated) covering
  every committed edit — drawing, moving, deleting, property and
  stackup changes. Opening or importing a project starts a fresh
  baseline.
- **Reference / comments layer**: the layer selector offers
  "✎ Reference" alongside the conductors. Geometry drawn there (any
  shape type, including transmission lines) renders as dashed outlines
  in the editor for annotation and reference — connector outlines,
  keep-outs, target dimensions — but is **completely invisible to the
  simulation**: excluded from validation (it may sit outside the
  board), from mesh generation, from the generated Octave script and
  from the Gerber export. Existing shapes can be moved to/from it via
  the Layer property; components and ports drawn while it is active
  fall back to the first conductor layer.
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

This is fine for a quick look, but the server dies with the terminal —
and with it goes the stage sequencing of a multi-excitation run. **For
anything longer than a few minutes, run it as a service instead.**

## Run as a service (recommended)

The server owns the simulation: it launches Octave, sequences the
excitation stages and writes the results to disk. Running it under
systemd makes it independent of terminals, SSH sessions and the browser,
so a simulation keeps going (and still saves its results) when you close
everything and walk away.

```bash
./scripts/install-service.sh
```

The script detects the repository path and `python3`, fills in
`openems-webgui.service` from those, installs it as a **systemd user
service**, enables and starts it, turns on *linger* so it also runs while
you are logged out, and waits until the HTTP endpoint answers before
reporting success. It is safe to re-run — that is also how you change the
port:

```bash
PORT=9000 ./scripts/install-service.sh          # different port
PYTHON=/usr/bin/python3.12 ./scripts/install-service.sh
```

Day-to-day control:

```bash
systemctl --user status openems-webgui      # is it running?
systemctl --user restart openems-webgui     # restart (kills a running sim!)
systemctl --user stop openems-webgui        # stop
journalctl --user -u openems-webgui -f      # live server log
```

Nothing is lost if you stop the service between runs: designs live in
`projects/`, and every completed run is written to disk before the
browser is ever involved (see *Nothing is lost when the browser goes
away* above).

## Update from git

```bash
./scripts/update.sh
```

The script, in order:

1. **Refuses to update while a simulation is running** — a restart would
   kill it. Wait for the run to finish, or override with `--force`.
2. Requires a clean working tree; `--stash` sets local changes aside and
   restores them afterwards.
3. `git pull --ff-only` (a diverged branch is reported, never
   auto-merged) and prints the commits you just received.
4. Reinstalls dependencies if `requirements.txt` changed.
5. Restarts the service if it is installed and running, then waits for
   the health check — printing the last log lines if it fails to come
   back.
6. Runs the fast test suite as a smoke test.

```bash
./scripts/update.sh --stash     # keep local edits across the update
./scripts/update.sh --force     # update even though a sim is running
```

Your data is never touched: `sims/` and `projects/` are gitignored, so
pulls cannot clobber designs, stored runs or results.

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
- `sims/run_*/` – one working directory per run (gitignored)
- `projects/<name>/` – stored designs, their run history and
  `folders.json` (gitignored)
- `scripts/install-service.sh` – install/refresh the systemd user service
- `scripts/update.sh` – pull from git, restart the service, smoke-test
- `openems-webgui.service` – unit template used by the install script

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
