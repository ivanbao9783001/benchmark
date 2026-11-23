# Result identity, integrity and performance evidence

Evaluation results pass result.schema.json before publication. The coordinator
binds task/operation/service/case identity, rejecting conflicting adapter identities.
Execution, evidence and validity enums are independent; VERIFIED performance or
accuracy requires COMPLETE evidence. Native request success alone cannot assert
performance validity.

`evaluation_type` selects the sibling contract: `perf` results carry
`performance_validity`/`performance`, `acc` results carry
`accuracy_validity`/`accuracy`/`accuracy_evidence`. The event mirrors the same
validity key. A result missing the branch required by its mode fails the schema.

Accuracy evidence reads `<case>/outputs/<timestamp>`: predictions must contain
exactly the case's effective sample count of success rows with gold and prediction,
and results must contain a finite 0-100 `accuracy`. The effective count is 198 when
`num_prompts` is absent, otherwise the case's `num_prompts`. The value is forwarded
from the native evaluator, never recomputed. Missing infer/eval logs or summary
leave `accuracy_validity=UNVERIFIED` with `ACC_INFER_LOG_MISSING`,
`ACC_EVAL_LOG_MISSING` or `ACC_SUMMARY_MISSING` recorded in accuracy-evidence.json;
the metric itself is still reported. Symlinks, escaping paths, malformed rows,
count mismatches and ambiguous run directories are hard failures.

When `num_prompts` is below 198 the run is a subset: `accuracy.num_samples=N`,
`accuracy.dataset_samples=198` and `accuracy.subset=true`; `scope` and `limitations`
must state `first N/198`. A complete subset without truncation can still be
`VERIFIED`, but it is a smoke/regression check only and must not be compared with
full-set accuracy.

Accuracy 还须核对输出截断：逐条比较原生预测的 `finish_reason=length`（若提供）或输出长度是否达到 case 的 `max_out_len`。截断样本占比 ≥ 5% 时，`accuracy_validity` 记 `UNVERIFIED`，在 accuracy-evidence.json 记录 `ACC_OUTPUT_TRUNCATED` 与截断计数，accuracy 仍转发但不得作为能力结论；0 < 占比 < 5% 时保持 `VERIFIED`，但必须在 `limitations` 写明截断样本数。证据文件完整性仍由 `evidence_status` 独立判定。

Use the event's result_sha256 when reading a stage result:

    deploy-eval-v2 --config control.json read-result --task-ref TASK --artifact-ref results/OP/stage-result.json --expected-sha256 DIGEST

Verification is optional for ordinary logs and explicit for result artifacts up to
4 MiB. It checks the entire file before returning a byte slice; continuation keeps
the same digest. Corruption returns RECOVERY_REQUIRED. verified_sha256 counts toward
the same final response byte budget. Large logs retain the bounded locator workflow.

Native details are streamed, hashed, and validated against request groups/counts.
Input/output token counts are recorded when present; unknown counts remain absent,
and differences from requested synthetic lengths are not automatic failures.
Native summaries are bounded to 1 MiB and checked for stage, units, finite values,
request consistency, planned maximum concurrency and throughput arithmetic.

When raw start_time/end_time seconds are available, timing analysis independently
checks summary duration. Steady analysis uses the second start reaching configured
concurrency through the final event at that concurrency, and checks selected count
and window duration. It also requires maximum full-run request latency below one
third of the stable window. Timing analysis is capped at 100,000 requests; exceeding
that bound does not reject the load or truncate native evidence, but leaves steady
performance UNVERIFIED. Missing timestamps (including versions storing them only
in another format), unsupported summary formats, inconsistent values and short
windows carry explicit reasons. No fabricated fallback metrics are emitted.

The observed format/semantics reference is AISBench/benchmark commit
aff1554354fbf19dccbdf2199cc3c22119f815a2:

- https://github.com/AISBench/benchmark/blob/aff1554354fbf19dccbdf2199cc3c22119f815a2/ais_bench/benchmark/calculators/base_perf_metric_calculator.py
- https://github.com/AISBench/benchmark/blob/aff1554354fbf19dccbdf2199cc3c22119f815a2/ais_bench/benchmark/calculators/stable_perf_metric_calculator.py
- https://github.com/AISBench/benchmark/blob/aff1554354fbf19dccbdf2199cc3c22119f815a2/docs/source_en/advanced_tutorials/stable_stage.md

The implementation is independent; no upstream source is imported at execution.
Summary evidence checks can verify ordinary or steady native request performance.
Configured server prefix metrics must also be verified. A requested shared-prefix
ratio still requires warmup/routing coverage that before/after aggregate counters
cannot prove; such a result remains UNVERIFIED with that scope explicit. This is an
evidence conclusion, not an assertion of zero cache hits or a reason to change load.

Synthetic postflight runs under the bound tester Python with local-only tokenizer
loading. It streams original native text, hashes details and records count,
min/max/mean token length and common prefix without regenerating data. Deviation
is diagnostic only; missing text/tokenizer is UNVERIFIED. The compatibility
reader delegates to this bounded code. External dataset rows are limited to
1 MiB, generation manifests to 16 MiB, within the preflight stage deadline.
