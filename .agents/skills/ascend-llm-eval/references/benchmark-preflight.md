# Bound benchmark preflight

The evaluator writes preflight-request.json and runs benchmark_preflight under the
tester.python path from frozen bindings. Its stdout/stderr go directly to
preflight.log through the owned process supervisor; the entire preflight shares
the case deadline and cancellation flag with formal execution. It does not install
packages, generate a replacement dataset, or contact the model endpoint.

Preflight verifies interpreter identity, executable availability and environment
location, exactly one installed distribution owning the configured console entry
point, tokenizer asset directory, native config imports, and file data or native
SyntheticDataset capability. Config is generated before this check without loading
remote data in the coordinator environment. The native dataset implementation owns
sample generation; support checks do not silently reduce load.

## Dependency floor

Tester environments must provide AISBench built from tag **v3.1-20260820-master
or later**. The `__version__` string is not a sufficient check: every
`v3.1-2026xxxx` tag reports `3.1.0`, but tags up to `v3.1-20260813-master` still
compute the steady stage with the old `WAVE_OFFSET` / last-request-end rule
(fixed by #464, commit `13908d2`, 2026-08-17). This CLI verifies steady summaries
against the post-#464 rule — second request reaching max concurrency through the
last event at that concurrency — so an older build keeps `total` intact but
fails steady verification with `SUMMARY_TIMING_MISMATCH`. The gitee `master`
snapshot is 3.0.0 and additionally lacks `PrefixLen` in `SyntheticDataset.load`.

Check the capability, not just the version, in `tester.python` before freezing:

```
python -c "import importlib.metadata as m; print(m.version('ais_bench_benchmark'))"
python -c "import inspect; from ais_bench.benchmark.calculators.stable_perf_metric_calculator import StablePerfMetricCalculator as C; src=inspect.getsource(C); print('OLD_STEADY_LOGIC' if 'WAVE_OFFSET' in src else 'OK')"
```

The version must be >= 3.1.0, the second command must print `OK`, and the
`ais_bench` executable next to that interpreter must come from the same
distribution, as preflight already enforces.

For `mode=acc`, the data check binds the supported native task catalog
(`gpqa_gen_0_shot_str`, `gsm8k_gen_0_shot_cot_str`), imports the task's native
dataset class, evaluator and postprocessors, and counts the evaluated split.
GPQA counts `gpqa_diamond.csv` with `csv.reader`, where quoted newlines make
physical line counts meaningless; GSM8K requires both `train.jsonl` and
`test.jsonl` and counts `test.jsonl`. Sample counts are measured at runtime, not
declared in code or schema, so the whole bound data must be present even for a
subset run. When the case declares `num_prompts`, it must be a positive integer
no greater than the counted samples, and AISBench `--num-prompts` performs the
first-N slice. A smaller dataset is not a substitute for `num_prompts`.
Both modes declare `summarizer` inside `with read_base():`; AISBench 3.1 builds
that enforce it reject a custom config that omits the parameter, and the top-level
import form is dropped when AISBench re-dumps the runtime config.

Only a successful child exit plus VERIFIED preflight.json permits AISBench to
start. Failures preserve preflight.log and use the normal stage failure/cleanup
path. Local tests cover injected distribution/capability evidence and a real Python
child rejecting a missing AISBench executable. They do not prove installed AISBench
compatibility on a target server; that remains part of authorized acceptance.
