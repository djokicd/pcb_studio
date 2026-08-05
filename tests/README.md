# Test suite

Two tiers:

**Fast unit tests** (no octave, a few seconds) — geometry helpers, mesh
generation, script generation/validation, Gerber/Excellon parsers and the
Flask API via the test client:

```bash
python3 -m pytest
```

**Simulation tests** (octave + openEMS, ~1–3 min each) — full FDTD runs of
canonical structures checked against closed-form expectations:

```bash
python3 -m pytest -m sim -v
```

| Test | Structure | Reference | Tolerance |
|---|---|---|---|
| `test_series_resistor_matches_theory` | 100 Ω series R in 50 Ω line | \|S11\|=\|S21\|=0.500 exactly | ±0.03 |
| `test_microstrip_z0_and_eeff` | 1.45 mm line, εr 4.6, h 0.8 (MSL ports) | Hammerstad: Z₀ 50.8 Ω, εeff 3.45 | S11 < −20 dB, εeff ±6 % |
| `test_quarter_wave_stub_notch` | 12 mm open stub, εr 3.66, h 0.254 (openEMS notch-filter tutorial cross-section) | f = c/(4L√εeff) ≈ 3.6–3.8 GHz | window 3.3–4.1 GHz, depth < −15 dB |
| `test_patch_antenna_resonance` | 32×40 mm patch, εr 3.38, h 1.524 (GUI demo / openEMS tutorial) | cavity model ≈ 2.5 GHz | window 2.25–2.75 GHz |

Simulation working directories are created under `tests/_work/<tag>/` and
contain the generated `pcb_sim.m` plus all solver outputs for debugging a
failed run.
