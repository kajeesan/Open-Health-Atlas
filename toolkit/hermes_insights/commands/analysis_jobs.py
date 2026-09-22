"""CLI ownership of the existing private analysis-job wrapper."""

from .. import analysis_jobs as analysis_engine


def analysis_job_cmd(context, args, handlers):
    """Delegate queue operations without changing the analysis engine."""
    return analysis_engine.cli(
        args.cmd,
        context.database,
        args,
        handlers,
        context.cli_path,
    )
