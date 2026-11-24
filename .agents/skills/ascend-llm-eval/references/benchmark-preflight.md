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
