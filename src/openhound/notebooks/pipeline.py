import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path
    from dlt._workspace.cli.utils import list_local_pipelines
    from dlt._workspace.helpers.dashboard.utils.pipeline import get_pipeline
    from dlt._workspace.helpers.dashboard.utils.visualization import (
        load_package_status_labels,
    )
    from dataclasses import dataclass
    from datetime import datetime
    from openhound.core.app import DEFAULT_LOOKUP_FILE
    from openhound.core.manager import CollectorManager
    import altair as alt
    import duckdb
    import marimo as mo
    import polars as pl
    import os

    return (
        CollectorManager,
        DEFAULT_LOOKUP_FILE,
        Path,
        alt,
        dataclass,
        datetime,
        duckdb,
        get_pipeline,
        list_local_pipelines,
        load_package_status_labels,
        mo,
        os,
        pl,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # OpenHound Pipeline Dashboard

    Inspect OpenHound collection performance and preview their OpenGraph node representation.
    Select a completed `*_collect` pipelin and choose a schema and table to inspect. The matching OpenHound extension is selected automatically based on the schema name. For extensions that use lookup data during conversion, run `preprocess` first so `lookup.duckdb` is available.
    """)
    return


@app.cell
def _(Path):
    DEFAULT_PIPELINE_PATH = Path("~/.dlt/pipelines").expanduser()
    return (DEFAULT_PIPELINE_PATH,)


@app.cell
def _(DEFAULT_PIPELINE_PATH, list_local_pipelines, mo):
    dlt_pipeline_dir, all_dlt_pipelines = list_local_pipelines(DEFAULT_PIPELINE_PATH)
    selected_pipeline = mo.ui.dropdown(
        options=[pipeline["name"] for pipeline in all_dlt_pipelines if pipeline["name"].endswith("collect")],
        label="Choose pipeline",
    )
    selected_pipeline
    return (selected_pipeline,)


@app.cell
def _(DEFAULT_PIPELINE_PATH, get_pipeline, mo, selected_pipeline):
    # TODO: This has to be modified to utils.pipeline() when updating to the latest version of DLT dashboards
    mo.stop(not selected_pipeline.value, "Select a pipeline to continue")
    dlt_pipeline = get_pipeline(selected_pipeline.value, DEFAULT_PIPELINE_PATH)
    return (dlt_pipeline,)


@app.cell
def _(dataclass, datetime, dlt_pipeline, pl):
    last_trace = dlt_pipeline.last_trace

    pipeline_success = True
    all_traces = []

    @dataclass
    class TraceStep:
        name: str
        started_at: datetime
        finished_at: datetime
        duration_ms: float
        pipeline: str = "last"

    for step in last_trace.steps:
        if step.step_exception is not None:
            pipeline_success = False

        if not step.step == "run":
            all_traces.append(
                TraceStep(
                    name=step.step,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    duration_ms=(step.finished_at - step.started_at).total_seconds()
                    * 1000,
                )
            )
    traces_df = pl.DataFrame(all_traces)
    return last_trace, pipeline_success, traces_df


@app.cell
def _(last_trace, load_package_status_labels):
    _ = load_package_status_labels(last_trace)
    return


@app.cell
def _(alt, mo, traces_df):
    pipeline_duration_chart = mo.ui.altair_chart(
        alt.Chart(traces_df)
        .mark_bar()
        .encode(
            x="duration_ms",
            y="pipeline",
            color="name",
        ).properties(height=30, width="container")
    )
    return (pipeline_duration_chart,)


@app.cell
def _(
    dlt_pipeline,
    mo,
    pipeline_duration_chart,
    pipeline_success,
    selected_pipeline,
):
    trace_title = mo.md(f"## Pipeline stats: {selected_pipeline.value}")
    pipeline_destination = mo.stat(
        value=dlt_pipeline.destination.destination_type, label="Destination"
    )
    pipeline_status = mo.stat(
        value="Success" if pipeline_success else "Failed", label="Status"
    )
    last_dataset = mo.stat(value=dlt_pipeline.dataset_name, label="Last dataset")
    pipeline_basic_state = mo.hstack(
        [pipeline_status, last_dataset, pipeline_destination], gap="2rem"
    )
    pipeline_basic_stats = mo.vstack(
        [trace_title, pipeline_basic_state, pipeline_duration_chart]
    )
    pipeline_basic_stats
    return


@app.cell
def _(dlt_pipeline, mo):
    # Available schemas
    selected_schema = mo.ui.dropdown(
        options=dlt_pipeline.schema_names, label="Choose schema"
    )
    return (selected_schema,)


@app.cell
def _(dlt_pipeline, mo, selected_schema):
    # Load the dataset based on the selected schema
    dlt_dataset = dlt_pipeline.dataset(schema=selected_schema.value)

    # Available tables for schema, excluding the built in _dlt tables
    dataset_tables = [
        table for table in dlt_dataset.tables if not table.startswith("_dlt")
    ]
    selected_table = mo.ui.dropdown(options=dataset_tables, label="Choose a table")
    return dlt_dataset, selected_table


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Dataset preview
    Select a dataset and table to inspect the resource schema and show a preview of the collected resources
    """)
    return


@app.cell
def _(mo, selected_schema, selected_table):
    data_filters = mo.hstack([selected_schema, selected_table])
    data_filters
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Table schema
    The table schema displays the available columns and their datatypes
    """)
    return


@app.cell
def _(dlt_dataset, dlt_pipeline, mo, os, pl, selected_table):
    mo.stop(not selected_table.value, "Select a table top continue")
    last_load_info = dlt_pipeline.last_trace.last_load_info.asdict()
    last_fs_destination = last_load_info["destination_displayable_credentials"]
    os.environ["BUCKET_URL"] = last_fs_destination
    dlt_table = dlt_dataset.table(table_name=selected_table.value)
    available_columns_df = pl.DataFrame(list(dlt_table.schema["columns"].values()))
    available_columns_df
    return (last_fs_destination,)


@app.cell
def _(Path, last_fs_destination, pl, selected_schema, selected_table):
    dataset_path = (
        Path(last_fs_destination.replace("file://", ""))
        / selected_schema.value
        / selected_table.value
    )
    table_df = pl.read_ndjson(dataset_path)
    return (table_df,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Data preview
    Select a sample count to preview the collected resources and the OpenGraph representation.
    """)
    return


@app.cell
def _(CollectorManager):
    available_collectors = CollectorManager.from_entrypoint(load_sources=True)
    collector_options = {
        collector.name: collector for collector in available_collectors.collectors
    }
    return (collector_options,)


@app.cell
def _(collector_options, mo, selected_schema):
    mo.stop(not selected_schema.value, "Select a schema")
    mo.stop(
        selected_schema.value not in collector_options,
        f"No loaded extension matches schema '{selected_schema.value}'",
    )

    selected_model = mo.ui.dropdown(
        collector_options.keys(), value=selected_schema.value, label="Extension"
    )
    return (selected_model,)


@app.cell
def _(mo, table_df):
    mo.stop(table_df.height == 0, "Selected table has no rows")
    max_sample_count = min(100, table_df.height)
    sample_count = mo.ui.slider(
        start=1,
        stop=max_sample_count,
        label=f"Sample count (max {max_sample_count})",
        value=min(20, max_sample_count),
    )
    return (sample_count,)


@app.cell
def _(mo, sample_count, selected_model):
    mo.hstack([selected_model, sample_count])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    #### Raw resource
    """)
    return


@app.cell
def _(mo, sample_count, table_df):
    mo.stop(not sample_count.value, "Select a sample count")
    mo.stop(table_df.height == 0, "Selected table has no rows")
    sample_df = table_df.sample(n=min(sample_count.value, table_df.height))
    sample_df
    return (sample_df,)


@app.cell
def _(collector_options, mo, selected_model):
    mo.stop(not selected_model.value, "Select an extension")
    selected_extension = collector_options[selected_model.value]
    return (selected_extension,)


@app.cell
def _(selected_extension):
    extension_dlt_resources = selected_extension.dlt_resources
    table_to_asset = {
        resource.table_name: resource.validator.model
        for resource in extension_dlt_resources
        if resource.validator and resource.validator.model in selected_extension.assets
    }
    return (table_to_asset,)


@app.cell
def _(DEFAULT_LOOKUP_FILE, duckdb, mo, selected_extension):
    lookup_session = None
    if selected_extension.lookup_factory:
        mo.stop(
            not DEFAULT_LOOKUP_FILE.exists(),
            f"Run preproc before previewing graph output. Missing lookup file: {DEFAULT_LOOKUP_FILE}",
        )
        lookup_client = duckdb.connect(str(DEFAULT_LOOKUP_FILE), read_only=True)
        lookup_session = selected_extension.lookup_factory(lookup_client)
    return (lookup_session,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    #### As node
    """)
    return


@app.cell
def _(lookup_session, mo, pl, sample_df, selected_table, table_to_asset):
    mo.stop(
        selected_table.value not in table_to_asset,
        f"Selected table '{selected_table.value}' is not mapped to an OpenHound asset",
    )

    def as_node(row, model):
        parsed_model = model.model_validate(row)
        parsed_model._lookup = lookup_session
        parsed_model._extras = {}
        return parsed_model.as_node

    as_node_df = pl.DataFrame(
        [
            as_node(row, table_to_asset[selected_table.value])
            for row in sample_df.iter_rows(named=True)
        ]
    )
    as_node_df
    return


if __name__ == "__main__":
    app.run()
