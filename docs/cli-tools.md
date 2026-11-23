## Conversion recovery

If a DLT normalization worker pool breaks during conversion and its failed
package is still pending, OpenHound attempts one fallback using a single
normalization worker. It resumes the saved DLT packages and loads them using
the original destination, without collecting or extracting source data again.
The fallback temporarily overrides normalization worker settings for that
pipeline, including environment and TOML configuration, and restores them before
loading. Collection, preprocessing, and unrelated conversion errors keep their
existing behavior.

Logs identify the failed pipeline and package, the recovery worker count, and
the pipeline working directory. If the fallback fails, the exception propagates
and OpenHound does not explicitly delete pending DLT packages. Preserve the
pipeline directory, collected resource files, and lookup database before starting
another job or changing containers. DLT may have completed some packages before
the failure; recovery is not an atomic rollback of uploads.

A broken worker pool can result from a container memory limit or another abrupt
worker exit. Single-worker normalization reduces concurrency but does not
guarantee that the workload fits in memory. It cannot recover automatically if
the job process or container itself is killed.

This is recovery within the active job. There is no new recovery CLI or durable
per-job workspace in this increment. The shared
`openhound.core.recovery.resume_pending_pipeline` function accepts an already
configured DLT pipeline and resumes pending normalization/loading without
extraction. Callers must exclusively own its state and coordinate the BHE job
lifecycle; it must not run alongside the scheduler on the same pipeline.
