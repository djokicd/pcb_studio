# OpenEMS PCB Studio

A browser-based GUI for setting up and running [openEMS](https://openems.de)
FDTD simulations of planar PCB structures via GNU Octave.

Draw or import a board, let the tool build a graded FDTD mesh from the
geometry, run the solver from the browser, and read the results — from
S-parameters and Smith charts down to animated surface-current density.

## Contents

- [Getting started](#getting-started) — [requirements](#requirements) ·
  [run](#run-it) · [run as a service](#run-as-a-service-recommended) ·
  [update](#update-from-git)
- [Designing a board](#designing-a-board) — [editor](#canvas-editor) ·
  [transmission lines](#transmission-lines) ·
  [stackup](#stackup-editor) · [vias](#vias) ·
  [components](#discrete-components) · [notes](#canvas-notes) ·
  [reference layer](#reference-and-comments-layer)
- [Importing fabrication data](#importing-fabrication-data) —
  [Gerber & drill](#gerber-and-excellon-import) ·
  [simplification](#geometry-simplification)
- [Meshing](#meshing) — [the Meshing tab](#the-meshing-tab) ·
  [graded meshing](#graded-meshing) ·
  [per-object meshing](#per-object-meshing)
- [Ports and devices](#ports-and-devices) —
  [lumped](#lumped-ports) · [MSL](#msl-ports) ·
  [Touchstone devices](#s-parameter-devices-touchstone)
- [Running a simulation](#running-a-simulation) —
  [run monitor](#run-monitor) ·
  [durability](#nothing-is-lost-when-the-browser-goes-away)
- [Results](#results) — [reflection & transmission](#reflection-and-transmission) ·
  [time domain](#time-domain) · [S-matrix](#s-matrix-raw) ·
  [current density](#current-density) ·
  [slice impedance](#slice-impedance-probe)
- [Projects, files and export](#projects-files-and-export)
- [Advanced tools](#advanced-tools) —
  [RF amplifier chain](#rf-amplifier-chain) ·
  [adding another tool](#adding-another-tool)
- [Verification](#verification)
- [Reference](#reference) — [repository layout](#repository-layout) ·
  [conventions](#conventions)

---

# Getting started

## Requirements

- Python 3 with Flask
- numpy, scipy and [scikit-rf](https://scikit-rf.org) — only for the
  advanced RF tools (`pip install -r requirements.txt`); everything else
  runs on Flask alone
- GNU Octave
- openEMS with its Octave/Matlab interface. Looked for under
  `$OPENEMS_MATLAB_ROOT`, `~/opt/openEMS/share`, `/usr/local/share`,
  `/usr/share` (first match wins).

## Run it

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
browser is ever involved (see
[Nothing is lost when the browser goes away](#nothing-is-lost-when-the-browser-goes-away)).

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

---

# Designing a board

## Canvas editor

A 2D top view in mm units with pictogram tools: rectangle, circle,
circle segment (pie sector), arc (curved trace), polygon (click points,
Enter/double-click closes), via, discrete component, lumped port and a
**measure tool** (X — drag between two points to read Δx, Δy and the
distance; Esc clears). Objects can be moved, resized via drag handles
(rects, circle radius, polygon vertices) or edited numerically.
Zoom/pan, keyboard shortcuts.

### Snapping

Arbitrary snap-grid resolution (any mm value, 0 = off), restrictable to
x-only or y-only grid ticks, plus optional snapping to corners/vertices
of existing objects. The background grid always shows the configured
snap grid (coarsened by 2/5/10× when zoomed out).

### Selection

With the Select tool, dragging on empty canvas box-selects every object
fully inside; the group moves/nudges/copies/deletes together (Ctrl+A
selects everything). Esc in any other tool returns to Select.

### Undo / redo

Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z, also in the Edit menu): full-project
snapshot history, 80 steps, deduplicated, covering every committed edit —
drawing, moving, deleting, property and stackup changes. Opening or
importing a project starts a fresh baseline.

### Editor conveniences

Ctrl+C/X/V copy, cut and paste the selected object (repeated pastes
cascade; Ctrl+D duplicates), the right panel is resizable via its drag
handle, and all confirmations and notices use in-app dialogs/toasts
(native browser dialogs are suppressed in some embedded browsers).

### Menu bar and panels

File / Edit / View / Tools / Help with the usual project, clipboard and
view commands, keyboard-shortcut hints and a shortcuts reference under
Help. The Tools menu is grouped into Editing / Drawing / Placement /
Configuration / Verification, and the Tests view opens from
Tools → Verification tests… (it no longer occupies a permanent main-area
tab). Both side panels are resizable by dragging the splitter next to
them; the widths are remembered per browser.

### Themes and colors

Dark (default) and light theme (View → Light theme) covering the whole UI
including the canvas and charts, and a configurable palette
(View → Colors…) for the conductor-layer, port, MSL-port and device-pin
colors — both stored per browser.

## Transmission lines

The **T** tool: click centerline points (Enter/double-click finishes) to
draw a line of configurable width; interior corners are optionally
rounded with a configurable radius (sampled fillet), so curved routes and
meanders are easy. Hovering a line shows the cumulative electrical length
from its start to the cursor plus the total centerline length; the
properties panel edits width, corner radius and shows the length. The
stroked outline (mitered sides, round caps) is generated identically in
the editor and in the Python geometry, so the mesh and simulation see
exactly the drawn line.

## Stackup editor

Arbitrary layer stack (top→bottom) of conductor and dielectric layers —
thickness, εr, tan δ per dielectric, optional full copper plane per
conductor. Shapes/ports/components reference conductor layers; conductors
are modelled as zero-thickness PEC sheets at their stackup interface.

### Stackup manager

Tools menu, or the Manager… button in the Stackup tab: save the current
stackup to a browser library, **edit any saved stackup's layers in place**
(same layer editor as the Stackup tab), apply one to the project,
export/import stackups as JSON files, and star one as the **default
stackup for new projects** (used by File → New project). Drill files
import via File → Import drill file; Gerbers via the ⇪ button on a
conductor layer.

## Vias

Drill/pad diameter, connecting any two conductor layers (metal barrel +
pads on every crossed conductor layer).

## Discrete components

R / L / C lumped elements in 0402, 0603, 0805 or custom footprints,
0°/90° rotation, values in Ω / pF / nH (openEMS `AddLumpedElement` with
end caps).

### Modelling accuracy

Lumped-element boxes are automatically shrunk to the copper-free gap they
bridge, so the nominal value applies across the gap regardless of how the
body overlaps the pads; unconnected component ends are flagged as
design-rule warnings.

The element sheet is **lifted 0.2 mm above the copper plane** like a real
chip part, with vertical PEC terminals connecting its ends down to the
pads — copper crossing under a component body (a trace routed beneath a
resistor) therefore couples only capacitively instead of galvanically
shorting part of the distributed element (measured: a 100 Ω series
resistor with a strip crossing its gap reads −5.97 dB |S21| against
−6.02 dB ideal; the coplanar model read ~−3.5 dB, i.e. ~50 Ω). The lift
plane gets its own mesh line automatically.

Capacitors and inductors carry a **series ESR** (default 0.25 Ω for C,
0.3 Ω for L — typical for chip parts; editable per component, 0 restores
the ideal element), realised as a second lumped sheet in the second half
of the gap. This is not only realism: an *ideal* C or L forms an undamped
resonator with its mounting-loop parasitics — after the excitation ends,
the tank rings forever, the J(t) view shows energy circulating in the
component loops, and the energy decay plateaus without ever reaching the
end criteria. The ESR damps exactly that loop.

## Canvas notes

Collapsible plain-text annotations pinned to a board position (**N**).
Each note has its own **title** (always visible, also when collapsed) and
body text, folded away by clicking the note's triangle or double-clicking
it, plus a per-note **marker colour** (pin, border and triangle) chosen
from quick swatches or a colour picker — notes left on the default colour
follow the light/dark theme. Notes are drawn at a fixed size so they stay
readable at any zoom, are edited in the properties panel with live
preview, move/copy/delete like any other object, and are saved with the
project — but they are documentation only and never reach the mesh or the
generated Octave script.

## Reference and comments layer

The layer selector offers "✎ Reference" alongside the conductors.
Geometry drawn there (any shape type, including transmission lines)
renders as dashed outlines in the editor for annotation and reference —
connector outlines, keep-outs, target dimensions — but is **completely
invisible to the simulation**: excluded from validation (it may sit
outside the board), from mesh generation, from the generated Octave
script and from the Gerber export. Existing shapes can be moved to/from
it via the Layer property; components and ports drawn while it is active
fall back to the first conductor layer.

---

# Importing fabrication data

## Gerber and Excellon import

Per-layer Gerber (RS-274X subset: standard apertures, strokes, arcs,
regions) via the ⇪ button on a stackup conductor layer, and Excellon
drill files creating PTH vias.

Stroked draws — including the arc tessellation — are chained into native
**centerline traces** (one shape per drawn line, lightly decimated to
10 µm) instead of one rounded stroke polygon per segment, so imported
boards arrive with editable widths and mesh cleanly from the start.

The drill-import dialog asks how many **mesh lines each via** may pin per
axis — auto (full round staircase), 5 (centre + drill & pad edges),
3 (centre + drill edges) or 1 (centre only) — because a fence of 100+
stitching vias meshed in full detail can dominate the cell budget (on the
bundled CPW test board, drill-only import at 1 line/via meshes with ~21 %
fewer cells *and* a larger timestep). The choice is remembered, applied
to the imported vias, and editable per via afterwards (Meshing tab →
click the via → Mesh lines), so critical signal vias can be set back to
full detail individually.

A shared import offset keeps all imported files aligned; the board
outline grows to fit. Imported geometry meshes by bounding box to keep
cell counts sane.

## Geometry simplification

Tools → Simplify geometry… rebuilds chains of previously imported stroke
polygons into centerline traces (each stroke is validated by regenerating
its outline before it may join a chain), thins densely tessellated
polygon outlines with a Douglas–Peucker pass, and can resample curved
lines to an even chord length. Copper edges move by at most the chosen
tolerance (default 0.02 mm). The dialog previews shape/vertex counts and
the mesh size before/after; applying is one undo step. On the bundled CPW
test board this turns 236 stroke polygons into 15 traces and cuts the
shape list from 378 to 175.

### Overlapping copper

Two further passes clean up overlapping copper, which imports produce in
quantity and which costs mesh lines for nothing — every buried edge still
pins one:

- **Drop shapes covered by another** deletes any shape lying wholly
  inside another on the same layer (a pad buried in a pour). Nothing is
  rewritten, so this one is exactly lossless.
- **Merge overlapping shapes** replaces each overlapping group with its
  common outline, computed by a small pure-Python boolean union
  (`polybool.py`: edges are split at every crossing, each piece is
  classified by probing both sides — which is what makes the shared and
  collinear edges of stroke soup fall out correctly — and the survivors
  are chained into rings). Transmission lines stay out by default, since
  a trace carries its width parametrically and the mesher refines across
  it along its whole run.

Both are **verified before they are accepted**: every merge is re-checked
by sampling membership against the original shapes, and a group whose
union would need a *hole* (a ground pour around its slots) is left alone
rather than filled — filling one would short whatever the gap separates.
Where the whole group cannot merge, the pairs that can still do. On the
bundled board the full pass takes 378 shapes to 61 and the generated
Octave script from 618 polygon calls to 304, with copper membership
identical over 40 000 sample points.

---

# Meshing

The mesh is computed in Python and emitted into the script as explicit
line vectors, so **preview == simulation**: ranges, pinned lines and the
outside settings all apply to the real run the same way.

## The Meshing tab

Press **M**, or pick Meshing next to Editor and Results: everything about
the FDTD mesh in one dedicated view with its own mesh-only tool set — no
drawing tools, nothing that can change the geometry. A zoomable top view
shows the exact simulated x/y mesh lines over the dimmed board; the
status bar reads out the cell size under the cursor.

### Tools

Entering the tab swaps the left panel's drawing tools for the mesh tools,
so nothing that edits geometry is reachable while meshing (the object
list comes along, since picking an object there is how you reach its
overrides).

*Point* pins one x and one y line at a spot, *x line* / *y line* pin a
single line, and *x range* / *y range* drag out a density interval.
Placement **snaps to the existing geometry** — copper corners for points,
copper edges for single lines — with a live preview showing when a snap
is active. Pinned lines are exact: the coincidence merge pulls a nearby
geometry line onto the pin rather than averaging the pair away.

### Density ranges

Intervals of the x or y axis where no cell may exceed a chosen
resolution. A freshly dragged range starts at half the mesh actually
present in that interval, so it refines immediately; bands are then moved
and resized directly on the view.

**Outside the ranges** a separate resolution cap and grading ratio apply
to the board area no range covers — pin the density where it matters and
let the rest relax smoothly.

### Axis strips

Bottom (x) and left (y) strips summarise the whole meshed domain the way
the right-hand strip summarises z — mesh-line density, every configured
range and pinned line, and a bracket showing the part of the domain
currently on screen. The strips **own** the ranges and pinned lines: they
are selected there, moved by dragging the band, and resized by dragging
its edge. The board view is only ever about objects, so a click on copper
can never grab a band lying over it.

### Selection and bulk edits

Click objects, shift-click to add, or reach for the two marquee tools —
**Box** drags a rectangle, **Loop** draws a freehand lasso, which is what
you want for a curved via fence or a diagonal run (on the bundled
amplifier a corridor loop takes 25 vias where its own bounding box would
take 93). Both default to what they fully enclose; <kbd>Shift</kbd> adds
to the selection and <kbd>Alt</kbd> switches to "anything touched".
"all vias" / "all shapes" select in one go.

With several objects selected, the panel applies **local resolution, edge
refinement and per-via mesh lines to all of them at once** — dropping a
57-via fence from full detail to centre-line-only is one dropdown change
(721k → 596k cells on the bundled GCPW board).

## Graded meshing

Inside every gap between geometry lines the cell sizes start from the
fine detail at the gap ends and grow geometrically (setting **"Mesh
grading ratio"**, default 1.5, the openEMS-recommended value) up to the
local cap — so the fine mesh that captures transmission-line geometry
relaxes smoothly into the bulk instead of jumping (abrupt size jumps
cause spurious numerical reflections).

The z axis is graded the same way and **asymmetric**: fine first cells at
conductor faces that carry geometry (the strip side), coarser toward
plane-only faces (the bulk ground) and through the air. The Meshing tab's
statistics report the smallest cell (sets the timestep) and the **worst
adjacent-cell step** — on a typical imported CPW board the rework took
that step from ≈27× to ≈1.8×, and the microstrip benchmark's return-loss
floor improved from −31 dB to −37 dB.

### Coplanar gaps

Facing copper edges on the same layer within roughly a substrate height
of each other — CPW slots, launch gaps, series gaps, trace-to-pour
clearances — are detected as **slots** and meshed deliberately: both
edges get the metal-edge refinement pair (no line on the edge itself,
one at rt/3 into the metal, one at 2rt/3 into the gap, with rt tied to
the **gap** width) and a fine zone guarantees several cells across the
gap. Without this the number of cells in a 0.3 mm CPW gap — and with it
the effective gap width and the line impedance — depended on where the
graded fill happened to land, so a 50 µm geometry nudge could change
the simulated Z₀.

Slot edges are also protected from the coincidence merge: refinement
-region boundaries and other auxiliary lines can no longer drag a
ground-pour edge tens of µm off its true position.

The z mesh follows: on a board with slots, the first substrate cells at
signal faces match the slot resolution, because the gap field dives into
the substrate at the edge-singularity scale and coarse first z cells
read the slot capacitance high (Z₀ low, εeff high — worth ≈1.5 Ω on the
GCPW benchmark). Component element gaps are exempt from slot refinement:
lumped elements need exactly the cell structure their pinned lines
define.

Gaps at or below the **meshMerge** tolerance (default 0.1 mm) are
treated as import noise and left to the merge, as before. The whole
behaviour can be turned off per project in the Meshing tab ("Refine
coplanar gaps") when clearances don't matter and the cell budget does.

### Curved and oblique edges

Copper edges produce mesh lines that follow the actual edge: axis-aligned
edges pin exact lines, curves are sampled along their length and thinned
to the local resolution, so the staircase tracks the copper instead of
its bounding box.

**Curved transmission lines** (drawn traces and stroke chains merged by
the simplify tool) additionally get per-axis cross-width fine bands along
their whole run — a straight run refines across its width only, bends
refine both axes — extending half a width past the copper edge so a
coplanar-waveguide slot is resolved with the same fine cells.

### Via fences and pad economy

Via fences are resolved at pad scale (~3 cells across the pad), so 100+
stitching vias don't crush the cell budget; a per-via local resolution
remains available for critical signal vias.

The per-via **mesh-line economy** (auto / 5 / 3 / 1 lines per axis) also
drives the **round copper pad** on each via: an imported board carries
the pads as ordinary circles (from Gerber) beside the drilled barrels
(from Excellon), and it is the pad — its sampled outline plus its
cross-width refinement zone — that actually generates the dense
staircase. Concentric pads therefore inherit the via's setting, and a pad
can also be set on its own (for round copper with no via under it). On
the bundled 126-via CPW board, setting the fence to one line per via
takes the mesh from 2.88M to 1.86M cells (−35 %); before the pads
followed along, the same setting gave −2 %.

## Per-object meshing

Meshing tab → click an object. Every shape/port/via/component takes an
optional local mesh resolution; shapes support the metal-edge 1/3–2/3
refinement rule (offset capped by the feature size) as a tri-state:
**auto** (the default) enables it for transmission-line features — traces
and shapes narrower than 3 mm — and leaves pads, planes and Gerber
imports coarse; on/off override per shape.

Near-coincident mesh lines are merged (setting "Merge lines <", default
0.1 mm) to protect the FDTD timestep.

---

# Ports and devices

## Lumped ports

z-directed between two conductor layers or in-plane (x/y) sheet ports;
per-port impedance.

**Any subset of ports can be excited**: with several excited ports the
run launches one excitation per port (the lowest-numbered one carries the
field dumps) and merges the resulting S-parameter columns into one result
set, so e.g. S11, S21, S12 and S22 come from a single Run click without a
full S-matrix sweep. The exported .m script excites the primary port
only.

## MSL ports

Matched microstrip-line ports (openEMS `AddMSLPort`) with de-embedded
reference planes, available as their own toolbar tool. Drag one at a line
end; the chevrons point into the board (auto-set toward the nearest edge,
editable). The generator extends the strip, substrate and ground plane to
the absorbing boundary so the port launches into the true line
cross-section, places the feed in the launch run and the S-parameter
reference plane at the port's inner edge.

Use these instead of lumped ports when accurate line impedance / return
loss matters (a 50 Ω test line measures S11 < −30 dB with MSL ports vs
≈ −25 dB with lumped ports). Requires an absorbing boundary (MUR/PML-8);
S-parameters are referenced to the port's "Ref. impedance".

**Not for coplanar waveguide.** The MSL port's mode template (voltage
strip-to-plane, current around the strip) does not fit a CPW/GCPW mode
whose return current flows in the coplanar pours: the wave decomposition
turns inconsistent (|S11|² + |S21|² comes out above 1) and the apparent
Z₀ reads far too low. Measure CPW lines with a lumped port and a
deliberately mismatched termination resistor instead — the quarter-wave
dip gives Z₀ = √(R·Zin_min) — which is exactly how the `gcpw_z0`
verification benchmark does it.

## S-parameter devices (Touchstone)

Black-box multiport devices — e.g. a transistor .s3p — placed in the
circuit via EM/circuit co-simulation. Put a lumped port at each device
pad, upload the .sNp to the server library (Simulation tab) and map
device pins to port numbers.

The run then performs one excitation per port to extract the full board
S-matrix and folds the device networks in circuit space
(S_ext = See + Sec·Sd·(I−Scc·Sd)⁻¹·Sce); the Results tab shows the
combined external S-parameters, with the raw board matrix
(`board_full.sNp`), the combined network (`combined.sNp`) and the
device-less column (`sparams_board.csv`) downloadable. Device data is
interpolated onto the sweep (clamped with a warning outside its range);
pin ports must match the file's reference impedance.

Device rows offer a 👁 preview of the file switchable between the |S| dB
curves and a **Smith chart of the reflection coefficients** (S11, S22, …)
— directly comparable against datasheet Smith figures. The file's leading
`!` header comments are parsed and the **measurement / bias condition
line is shown** in the device row and the preview title: vendor
S-parameter sets ship one file per bias point (e.g. BFG25A at
Vce=1V/Ic=0.1mA vs 1V/1mA), and using the wrong-bias file makes S11 look
completely different from the datasheet curve while the tool is parsing
it perfectly.

Each pad can be named (gate/drain/source…) — pin ports are drawn violet
in the editor with their `ref.pad` label. Full-matrix runs (automatic
with devices, or opt-in via "Full S-matrix" in the Simulation tab) export
every external port's own reflection, and the Reflection card then shows
all S_ii together — legend chips toggle traces in both the dB view and
the multi-trace Smith chart.

Note this is circuit-level embedding — EM coupling between the device
package and the board is not modelled, exactly as in standard EM/circuit
co-simulation flows. A synthetic `fet_demo.s3p` ships in `devices/`.

---

# Running a simulation

## Run monitor

Octave/openEMS runs as a subprocess with a graphical monitor — progress
bar with ETA, stat tiles (timestep, speed, energy, cells), a live
energy-decay chart with the end-criteria target line and a solver-speed
chart. Both charts autorange their x axis to the timesteps actually
solved (labelled `timesteps (0 – N)`) rather than the configured limit.

All solver output is parsed: engine facts (version, engine type, threads,
FDTD size, timestep, Nyquist rate, excitation length, final speed) appear
in an Engine table and every solver warning is surfaced in a highlighted
box; non-converged runs are labelled as such. The raw terminal log stays
collapsed by default (auto-expands on errors). Runs can be stopped.

### Multi-excitation runs

These get **one tab per excitation**: every solver run restarts at
timestep 0, so their samples, engine facts and warnings are kept in
separate records instead of being concatenated into overlapping curves.
The tab of a stage is selected automatically as that stage starts, a
manual selection is then left alone, and tabs mark stages that produced
warnings or failed to converge — plus whether each one is queued,
solving, finished or cancelled.

### Solving excitations in parallel

An S-parameter device, the full-S-matrix option or several excited ports
turn one Run into several solver runs. **Parallel excitations** in the
Run tab solves that many side by side instead of one after another; the
tab shows how many the design needs and what the setting will do, and
the run monitor reports `2/4 excitations · 3 running in parallel`. The
stages are wholly independent — separate directories, no shared state —
so this is purely a scheduling change, and on a short run of the bundled
4-port amplifier the combined S-matrix came out **bit-identical** to the
sequential one (max difference 0.000e+00 across all 51 frequencies).

Identical output is not guaranteed in general, though, and the reason is
worth knowing: openEMS checks its energy end-criterion on wall-clock
paced blocks, so a busier machine evaluates it at different timesteps and
stops at a different residual energy. On a longer run of the same board
the sequential stages ran to −70…−73 dB while the parallel ones stopped
at −56…−60 dB — both far past the −45 dB asked for — and the S-matrices
then differed by 1.1e-3 (≈ −60 dB relative). That is residual energy,
not a scheduling error; solve sequentially if you need a run reproducible
to the last bit.

**Threads are left to openEMS.** It benchmarks the actual mesh at
startup and picks the thread count that is fastest for it, which beats
splitting the cores by hand: on a small board it settles on one thread,
so N instances simply occupy N cores. Measured on the bundled amplifier
(4 excitations, 8 cores), openEMS chose 1 thread and the run took 34 s
sequentially, **17.5 s at 2× and 17.0 s at 4×**. Forcing `cores / N`
threads instead made the 2× case *slower than sequential* (42 s), which
is why the split is not the default. A **Threads per excitation** cap is
available for boards where memory bandwidth, not cores, is the limit.

The cost is memory: each excitation is a full openEMS instance holding
its own field arrays, so peak memory multiplies by the parallel count.
A failing excitation stops its siblings rather than burning cores on a
run whose combination can no longer be assembled.

## Nothing is lost when the browser goes away

Every run directory stores the exact model that produced it
(`sims/run_*/project.json`), and the moment a run completes the server
**automatically attaches its results to the project on disk** — no
browser needed.

For long unattended simulations, run the server as a systemd user service
(`./scripts/install-service.sh`, see
[Run as a service](#run-as-a-service-recommended)) so it outlives
terminals and login sessions.

### Keep working while it solves

The run belongs to the server, not to the browser tab, so a simulation
is not a modal state: **open another project, edit it, preview its mesh,
or browse any past run's results while the solver works.** A chip in the
top bar shows the running project and its progress from anywhere and
takes you back to the monitor in one click; it turns amber when the run
belongs to a project other than the one you have open, and the Run tab
says whose run it is.

A run that finishes while you have moved on **will not pull you out of
what you are doing** — instead of switching the view to its results, it
posts a notice telling you where to find them (File → Browse runs…, or
the project's ▤ results browser). The Run button stays disabled with the
name of the project currently solving, since the server runs one
simulation at a time.

### Session restore

On page load the GUI restores the session in three layers: a run still
solving re-attaches to the live monitor; a run that finished while the
browser was away loads its results from the server's run state;
otherwise a pointer kept in localStorage reloads the last results this
browser had open (and the Results/Editor view you were on).

**File → Browse runs…** lists every `sims/run_*` on disk — newest first,
with project name and a results badge — to reopen any past run's results
or reload the design that produced it (✎).

---

# Results

Results open in a dedicated Results tab in the main area (the GUI
switches to it when a run finishes), all interactive (hover tooltips) and
pop-out-able to a large modal.

## Reflection and transmission

**Reflection**: magnitude (dB) plot, **Smith chart** (Γ and denormalised
impedance readout on hover) or **VSWR** — the standing-wave ratio
(1+|Γ|)/(1−|Γ|), floored at 1:1 with a soft 20:1 ceiling so one
near-total reflection cannot flatten the rest; a port with |Γ| ≥ 1
(active or unstable) reads ∞. The reflection CSV export carries a VSWR
column alongside re/im/dB.

**Transmission**: magnitude (dB) plot or **polar plot** (|S| dB vs
phase).

Raw `sparams.csv` (complex S and Zin) is downloadable.

## Time domain

The raw port signals of every port (lumped and MSL — for MSL ports the
measurement-plane probes are used). Any combination of voltages and
currents plots on one shared axis: currents are drawn as i(t)·Z₀ so they
are directly comparable with the voltages (equal traces = matched wave);
tooltips report the raw current in amperes. Legend chips toggle each
signal (voltages shown by default, currents hidden).

The signals span the **full run duration** (decimated to ≤4000 points for
plotting), and in a multi-excitation run an excitation selector on the
card switches between the recorded u/i of **every** solver stage — the
per-stage probe files are stored with the run, so this works for past
runs too. The J(t) recording window may span up to 500 ns (frames land at
the engine's Nyquist interval, so whole-run windows stay small), and
switching the current-density view to another excitation shows a loading
veil instead of silently keeping the previous fields on screen.

## S-matrix (raw)

For multi-excitation runs (the "Full S-matrix" option, a multi-port
excitation selection, or any run with devices) the complete N×N matrix
assembled from every excitation, shown as a clickable table at a
selectable frequency — each entry as dB∠deg, mag∠deg or real/imag.

Clicking a cell toggles that S_ij as a trace on the chart below, so any
subset (a whole row, the diagonal, a reciprocity pair) can be compared
directly, in **magnitude (dB), on a Smith chart, as VSWR or on a polar
plot**; the table cells themselves also switch to VSWR. Smith and VSWR
describe a reflection coefficient, so they use the diagonal entries S_ii
only — transmission entries stay listed but are greyed out with a note
(and show "—" in the VSWR table); the polar plot takes any entry.

A selector switches between the **raw board matrix** (before device
networks are folded in) and the **resulting network**, the whole matrix
exports to CSV, and the untouched per-excitation `sparams.csv` of each
FDTD run is linked for download. Handy as a sanity check: S_ij = S_ji on
a reciprocal board.

## Current density

Frequency-domain rot(H) dumps per conductor layer, animated as
Re{J·e^{jφ}} over one RF period with play/pause and a phase slider; layer
and frequency selectable. Dump frequencies accept a GHz list or `all`
(21 points across the sweep), and any *arbitrary* in-band frequency can
be viewed — the complex field is linearly interpolated between the two
nearest dumped points (marked "interpolated"). The animation exports as a
looping GIF (dependency-free encoder, one RF period in phasor mode or the
recorded frames in J(t) mode).

The non-uniform FDTD grid is resampled with bilinear interpolation in
physical coordinates (toggleable — off shows the raw mesh cells), with a
color-scale bar. A **log** toggle switches the color scale to logarithmic
(the 40 dB below the peak), revealing weak return currents that the
default scale hides; the choice persists.

In a multi-excitation simulation **every solver run records its own
current density** — an excitation selector on the card switches between
them — and the processed dumps ride along when the run is stored with its
project, so the J viewer works for past runs too.

### Display modes

A **display selector** switches the frequency-domain view between the
phasor animation and three static images — **amplitude** (the |J|
envelope), **phase** (hue wheel, blanked where no current flows) and
**amp · phase**, the combined map with the phase as hue and the amplitude
as brightness (interpolated wrap-safe via the value-scaled complex field)
— all with the same zoom, pan and cursor probe, and the choice is
remembered. The cursor readout lives in a fixed status strip under the
plot, so hovering never reflows the controls.

### Recorded J(t)

Time-domain rot(H) current density captured during a configurable window
(StartTime/StopTime, optional spatial subsampling), decimated to ~160
frames and played back like the port signals — switchable against the
single-frequency phasor view.

### Steady state with devices

With S-parameter devices, the excitation selector offers **steady
(folded)**: the true steady state of the combined network at each dumped
frequency, computed as the complex superposition of every excitation
run's field weighted by the incident waves the device networks impose
(the same fold math as the S-matrix combination), driven at the primary
port.

Verified with an ideal thru device bridging a gapped line: a single
excitation shows the current dying at the gap (downstream/upstream
≈ 0.003), the folded steady state restores continuity through the device
(≈ 0.97). Phasor-only — the per-stage time-domain frames share no phase
reference.

## Slice-impedance probe

Its own results card: arm "place cut" and click a straight run of line in
the Current-density view, and the complex standing-wave pattern of the
total current on the source side of the cut is fitted as
I(s) = A·e^{+jβs} + B·e^{−jβs} (β scanned, A/B least squares; the fit
window is clipped against port/component/via footprints and drawn as a
bracket).

Γ = −B/A at the cut gives the impedance looking into the chosen side,
drawn on its own **Smith chart over every dumped frequency** with a ring
marking the frequency shown in the current view (Z de-normalised by an
adjustable reference).

Validated against an open-ended line: |Γ| = 0.996 across 21 frequencies,
phase within 1° of −2βℓ, correct clockwise rotation; a matched port
measures |Γ| = 0.11. Looking toward the *driven* port is flagged — that
side is active, so the wave ratio is not a passive impedance.

## Saved run diagnostics

When a run finishes (or fails), the Run tab's diagnostic data — the
per-excitation energy-decay and solver speed series, the engine facts
table (timestep, FDTD size, excitation length, final speed…) and solver
warnings — is written to `diagnostics.json` next to the results and
stored with the run. Clicking any past run restores the plots and the
table exactly as they looked live, with a note naming the run and its
finish time.

## Arranging the results pane

With three or more ports, the legend chips on the Transmission and
Time-domain cards toggle individual traces on/off (persisted). Result
cards can be rearranged by dragging their ☰ grip and resized in both
directions via the resize corner; the pane is further configurable with
per-card show/hide toggles, a column-count selector and a "Reset layout"
button (layout persisted per browser).

Cartesian charts zoom by drag-selecting a range or with the mouse wheel
(double-click resets); Smith/polar charts and the current-density map
zoom with the wheel and pan by dragging. Every chart exports to PNG, and
the S-parameter/time-domain charts also export CSV.

---

# Projects, files and export

## Server-side projects

Save stores the project on the server (`projects/<name>/project.json`)
together with a copy of the latest run's results (S-parameters, port
signals, current-density exports — bulky raw field dumps excluded); Open
lists the stored projects (with a "results" badge, delete supported) and
restores both the design and its results.

"Export cfg" downloads the project JSON, and the Open dialog can import
one. The project name in the top bar is a read-only display: a project
reads "Untitled project" until it is named by File → Save / Save as…, or
by opening or importing one.

## Project browser with folders

File → Open shows the complete tree of stored projects, organized into
**named folders with optional tags, nested to any depth**. Drag a project
onto a folder (or a folder onto a folder) to move it; drop on empty space
for the top level. Folder rows expand/collapse (state remembered) and
offer ＋ subfolder, ✎ rename, 🏷 tags and ✕ delete — deleting a folder
keeps its contents, moving them up one level, and moving a folder into
its own subtree is rejected.

The hierarchy is a pure organizational overlay stored in
`projects/folders.json`: the on-disk project directories stay flat, so
run ids, stored results and the completion auto-attach are untouched by
any reorganizing.

**Save places the project in a folder**: the first Save and
File → Save as… ask for the project name *and* its destination folder,
with an optional "new subfolder" field that creates the folder on the
spot inside the selected one. Re-saving an existing project preselects
the folder it already lives in.

## Per-project run history

Each completion is stored as its own `projects/<name>/runs/<run_id>/` —
results *plus a snapshot of the editor state that produced them*, so
several runs of the same project coexist. The ▤ button on a project in
the Projects pane opens the project's **results browser**: every stored
run with date and excitation count — click to open its results, ✎ to
restore that run's exact design into the editor, ✕ to delete a stored
run. (An older single `results/` copy still appears as a "legacy" entry.)

The **Run tab in the right sidebar also lists the current project's past
runs** (stored ones plus any matching runs still only under `sims/`,
tagged "unsaved"), newest first with the open one highlighted — one click
away from re-opening any previous result while watching a new run.

## Loaded for review

The left sidebar switches between Design (tools + objects) and
**Loaded** — projects held for review *without* opening them in the
editor. Click one to show its latest results, tick "cmp" to overlay its
S-parameters on the current results (resampled onto the current sweep;
overlay traces get their own legend chips), ▤ to browse its runs, ✕ to
drop it. The project open in the editor heads the list. Add projects with
＋ add, or with ⊕ in the Open dialog. The list is remembered per browser.

## Gerber and drill export

File → Export Gerber + drill produces a zip with one RS-274X file per
conductor layer (shape outlines as filled G36/G37 regions — exactly the
polygons the simulation uses — via pads as circle flashes on the layers
the via crosses, plane layers as a full-board region), a board-outline
file, and an Excellon drill file for the vias. The generated files
re-import cleanly through the tool's own Gerber/Excellon parsers. Note:
plane regions carry no thermal reliefs or clearances.

## Script and project export

Export the self-contained Octave `.m` script; save/load projects as JSON
(auto-saved to browser localStorage, old single-substrate projects are
migrated automatically).

---

# Advanced tools

Design utilities that stand beside the PCB editor rather than inside it:
they open from **Tools → Advanced tools**, work on their own inputs, and
do not need a board loaded. They read the project's Touchstone library
(`devices/`), so anything already imported for an S-parameter
co-simulation is immediately available to them.

## RF amplifier chain

    [Z0 source] — input match — device (.s2p) — output match — [Z0 load]

Pick a two-port from the library, set the reflection coefficients Γ_S and
Γ_L you want presented to the device, and choose how each matching
network is realized — lumped L-section, shunt open/short stub + line,
quarter-wave transformer, or an ideal lossless reference. The networks
are synthesized to hit those Γ at the design frequency and then evaluated
over the device's whole measured band, so the off-frequency behaviour of
each topology is visible rather than assumed.

- **Schematic**: the matched chain drawn end to end — source, the
  synthesized elements with their values (series/shunt L and C, stub and
  line lengths in mm and λ), the device with its stabilization parts, and
  the load, annotated with the Γ actually achieved.
- **Plots**: gain and match (|S21|, MAG/MSG, |S11|, |S22|), SWR with K
  and µ, and the reflections on a Smith chart.
- **Best match @ f0** applies the simultaneous conjugate match, or — for
  a conditionally stable device, where no such match exists — a
  stable-region gain search.
- **Stabilization**: series/shunt resistors at either port, an emitter
  inductor and DC-blocking capacitors, applied before matching. The panel
  reports K and µ and warns when the worst case over the measured range
  is below 1.
- **Optimize for band** searches Γ_S/Γ_L (and optionally the
  stabilization network itself, 9 parameters) so the *realized* chain
  meets minimum-gain and maximum-SWR targets at every point in a band,
  while requiring unconditional stability over the device's entire
  measured range — an amplifier must not oscillate out of band either.
  It runs in the background with live progress and reports honestly when
  a target is infeasible. On the bundled BFG25A over 0.8–1.2 GHz it finds
  ≈6 Ω/298 Ω input, 3 Ω/356 Ω output loading and 0.43 nH of emitter
  inductance: 9.6 dB at f0 against a 9.8 dB MAG, SWR 1.45/1.30, and
  K ≥ 1.25 across the whole file.

The maths comes from a standalone [scikit-rf](https://scikit-rf.org)
application, vendored here as `advtools/rfamp/` and running on scikit-rf
itself — the two-port container, the S/Z/ABCD conversions, the cascade
and the transmission-line media are all upstream code, so the tool
behaves exactly like the original program and stays easy to re-sync with
it.

This is the one part of the project with third-party dependencies:
`numpy`, `scipy` (the band optimizer's differential evolution) and
`scikit-rf`. They are needed only when an RF tool is opened — the editor,
the mesher and the openEMS run sequencing still import nothing beyond
Flask. Install them with

```bash
pip install -r requirements.txt
```

On a distribution python that refuses to install into itself (PEP 668,
"externally managed environment"), install into the user site instead —
the service python picks it up from there:

```bash
pip install --user --break-system-packages -r requirements.txt
```

scikit-rf 2.x requires numpy ≥ 2, so `requirements.txt` caps it at
`<2` for the numpy 1.x that ships with most distributions; drop the
ceiling once your numpy is 2.x.

## Adding another tool

A tool is one module in `advtools/tools/` exposing

```python
TOOL   = {'id': ..., 'name': ..., 'description': ..., 'group': ...}
def schema():                     # fields, actions, panels
def handle(action, payload):      # the actions
```

The registry discovers it, the menu lists it and the server routes to it
through the same two endpoints (`/api/tools`, `/api/tools/<id>/<action>`)
— no server, menu or UI edits. Controls and results are declarative: the
browser owns the widgets (`number`, `select`, `check`, `freq`, `gamma`)
and the panels (`table`, `text`, `chart`, `smith`, `schematic`), so a new
tool describes what it wants rather than shipping its own interface. An
action marked `job` runs in the background with polled progress.

The window lays itself out from the same description. Controls sit in a
column whose `group` entries fold — `{'group': ..., 'open': False}` starts
a section closed — while the action buttons and the status line stay
below it, out of the scroll. Panels marked `'pin': True` share a strip
along the top and stay visible; the rest become tabs, so one plot gets
the whole area instead of a quarter of it, with an **All** tab for the
side-by-side view. Which tab you left open and which sections you folded
are remembered per tool.

---

# Verification

## Built-in tests

The Tests view (Tools → Verification tests…) runs the test suite from the
GUI — fast code-level checks (parsers, mesh, script generation, API) and
four benchmark simulations of canonical structures (series resistor,
microstrip Z₀/εeff, quarter-wave stub notch, patch resonance), each
showing a reference-vs-obtained comparison table with acceptance windows.
The same cases run headless via `python3 -m pytest -m sim` (see
`tests/README.md`).

## Design rule warnings

Objects outside the board outline and z-ports that don't land on copper
are flagged in the editor and before each run.

---

# Reference

## Repository layout

- `server.py` – Flask app: static UI, `/api/script`, `/api/run`,
  `/api/status`, `/api/stop`, `/api/mesh` (preview),
  `/api/results/<run>/…` (sparams.csv, jdumps list, per-layer current
  density binaries)
- `scriptgen.py` – model validation + Octave script generation
- `geometry.py` – stackup z-positions, shape outlines, footprints
- `meshlines.py` – mesh line generation (shared by preview and script)
- `simplify.py` – stroke chaining, outline thinning, overlap cleanup
- `advtools/` – advanced tools: registry, `rfamp/` (scikit-rf based
  matching, stability, optimization), `tools/` (one module per tool)
- `polybool.py` – polygon boolean union used by the overlap cleanup
- `static/` – vanilla JS UI: `js/editor.js` (canvas editor),
  `js/meshtab.js` (Meshing view), `js/charts.js` (dB/Smith/polar +
  modal), `js/jview.js` (current density animation), `js/app.js` (state,
  panels, run control)
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
