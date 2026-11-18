# Replicating McDaniel et al. 2019 on BioGears 8.2.0

**Paper:** M. McDaniel, J. M. Keller, S. White, A. Baird (2019). *A Whole-Body Mathematical Model of Sepsis Progression and Treatment Designed in the BioGears Physiology Engine.* Front. Physiol. 10:1321. [doi:10.3389/fphys.2019.01321](https://doi.org/10.3389/fphys.2019.01321) ([PMC6813930](https://pmc.ncbi.nlm.nih.gov/articles/PMC6813930/))

**Scope (decided 2026-11-02):** the three core experiments, on BioGears 8.2.0 only, through pybiogears:
1. the course of mild, moderate and severe infection over 48 h, with an uninfected control (paper Figs 4–5);
2. the untreated progression of severe infection, with its event timings (Figs 6–7);
3. the 1000 mL saline challenge (Fig 9).

Lymph and tissue-fluid quantities are recorded throughout, because the paper's endothelial-leak / oncotic-pressure / lymph model is what turns inflammation into hypovolemia.

**Why the results may differ from the paper.** The paper's code is closest to BioGears 7.3.0: the infection action was added 2019-07-12, the paper was published 2019-10-18, and 7.3.0-RC1 is dated 2019-11-15. Up to 8.2.0 there have been 93 further commits to `BloodChemistry.cpp` and `Tissue.cpp`. Differences between this replication and the paper therefore mix model drift since 2019 with anything not reproducible from the paper's description; with 8.2.0 alone they can't be told apart.

## Protocol, and where each part comes from

| element | value | source |
|---|---|---|
| patient | StandardMale, from `states/StandardMale@0s.xml` | BioGears' infection scenarios start from StandardMale; P5 showed the shipped state equals 8.2.0's own initialisation to 1e-13 |
| infection | `InfectionData`, Location Skin, Severity Mild/Moderate/Severe, at t = 0 | paper; BioGears 8.2.0 `Scenarios/Patient/Infection*.xml` |
| MIC | 16 mg/L | paper (Fig 4 caption). The shipped scenarios use 8 mg/L. MIC only acts through antibiotics, and these runs have none |
| duration | 48 h | paper |
| water and meals | 150 mL of water hourly, meals at 4, 9, 24, 28 and 33 h, the same for every run | `nutrition_schedule.json`, frozen from the shipped `InfectionModerate.xml`. The paper says only "periodic water and meal actions", with the same schedule for the control. The shipped Severe scenario adds meals at 40 and 45 h; it is not used, so all runs stay comparable |
| control | the same schedule, no infection | paper ("control = uninfected patient on the same schedule") |
| fluid challenge | from the severe run's 44 h state: Saline 500 mL at 44.0 h and 500 mL at 44.5 h, each over 30 min (16.7 mL/min), observed to 46 h | paper: "1,000 mL saline as two 500 mL boluses at 44 and 44.5 h post-infection, over one hour, then one hour of observation". The shipped scenario gives them at 48 h at 50 mL/min |
| sampling | every simulated minute | — |

**Event definitions for the comparison (`analyze.py`).** A condition counts once it holds for at least 30 min.

| event | definition | paper's time (severe) |
|---|---|---|
| lactate | Aorta lactate > 2 mM | after 20 h |
| hypotension | SBP < 100 mmHg | 26 h |
| tachypnea | RR ≥ 22/min | — |
| sepsis | SBP < 100 **and** RR ≥ 22 (qSOFA-style; the paper says "SBP < 100 mmHg plus tachypnea") | ~30 h |
| septic shock | MAP < 65 **and** lactate > 2 mM | 45 h |
| oliguria | mean urine output < 0.625 mL/min | — |
| SVR | lowest value relative to baseline | about 60 % |

The paper doesn't define its tachypnea threshold; 22/min is the qSOFA value.

## Running (on the Mac: about 8 h of wall time per 48 h run on a 2-core cloud VM, so use the Mac)

```sh
cd pybiogears                                                    # your clone
source .venv/bin/activate
CMAKE_PREFIX_PATH=~/biogears-8.2.0-install pip install .        # rebuild: pybiogears changed since the Mac install
export BIOGEARS_RUNTIME=~/biogears-8.2.0-install/share/biogears/runtime
caffeinate -i python studies/mcdaniel2019/run_study.py all --jobs 4
python studies/mcdaniel2019/analyze.py
```

- `all` runs control, mild, moderate and severe in parallel (4 processes), then the challenge from severe's 44 h state.
- `caffeinate -i` keeps the Mac awake; a sleeping Mac pauses the runs.
- Progress: `tail studies/mcdaniel2019/out/*/log.txt`, which gets a line every 4 simulated hours.
- If a run is interrupted, run the same command again. Each run resumes from its last 4-hourly state; tested, a resumed run matches an uninterrupted one to 1.5e-9.
- `analyze.py` works on partial results at any time.

## Outputs (`out/`)

- `<run>/data.csv`: one row per simulated minute. The columns are:
  - the inflammatory model: blood and local pathogen, TNF, IL-6, IL-10, NO, tissue integrity;
  - vitals: HR, RR, core temperature, WBC, SBP, DBP, MAP, SVR, CO, SV;
  - blood volume, urine rate and mean urine output, aorta lactate and albumin, CVP;
  - extravascular and total body fluid;
  - lymph return (`LymphToVenaCava`), the 13 tissue lymph flows, the lymph pool, and muscle endothelial resistance (`MuscleE1ToMuscleE2`, which falls as tissue integrity falls).
- `<run>/events.csv` holds BioGears events; `log.txt` holds progress and errors; `state@<h>h.xml` are the checkpoints.
- `summary.md`, `fig4.png` … `fig9.png` and `fig_lymph.png` come from `analyze.py`.

## Tests done before the full runs (cloud, 2026-11-02)

- Control and severe for 15 min. All 42 requests connect. The infection runs: local pathogen 0 → 2.4e5, TNF and IL-6 rising. The control stays at baseline. The symptom events (nausea, headache, diarrhoea) are recorded.
- Resume: a severe run checkpointed at 0.05 h and resumed to 0.15 h matches an uninterrupted run to 1.5e-9.
- The challenge path, from a stand-in state: blood volume rises about 600 mL over the first bolus, and lymph return falls as CVP rises, as in P5.
- Speed: about 6× real time per engine on the cloud VM, unaffected by the sepsis model.
