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
    from dataclasses import asdict, dataclass, is_dataclass
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
        asdict,
        dataclass,
        datetime,
        duckdb,
        get_pipeline,
        is_dataclass,
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

    Inspect recent OpenHound collection runs and preview the OpenGraph representation of collected resources.

    Select a completed `*_collect` pipeline and choose a schema + resource table to inspect. The matching OpenHound extension is selected automatically from the schema name. For extensions that use lookup data during conversion, run `preprocess` first so `lookup.duckdb` is available.
    """)
    return


@app.cell
def _(Path):
    DEFAULT_PIPELINE_PATH = Path("~/.dlt/pipelines").expanduser()
    return (DEFAULT_PIPELINE_PATH,)


@app.cell
def _(DEFAULT_PIPELINE_PATH, list_local_pipelines, mo):
    dlt_pipeline_dir, all_dlt_pipelines = list_local_pipelines(DEFAULT_PIPELINE_PATH)
    collect_pipelines = [
        pipeline["name"]
        for pipeline in all_dlt_pipelines
        if pipeline["name"].endswith("collect")
    ]
    mo.stop(not collect_pipelines, "No collect pipelines found")
    selected_pipeline = mo.ui.dropdown(
        options=collect_pipelines,
        value=collect_pipelines[0],
        label="Collect pipeline",
        full_width=True,
        allow_select_none=False,
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
            x=alt.X("duration_ms", title="Duration (ms)"),
            y=alt.Y("pipeline", title=None),
            color=alt.Color("name", title="Step"),
            tooltip=["name", "duration_ms", "started_at", "finished_at"],
        ).properties(height=40, width="container")
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
    trace_title = mo.md(f"## Pipeline Overview: `{selected_pipeline.value}`")
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
    mo.stop(not dlt_pipeline.schema_names, "Selected pipeline has no schemas")
    selected_schema = mo.ui.dropdown(
        options=dlt_pipeline.schema_names,
        value=dlt_pipeline.schema_names[0],
        label="Dataset schema",
        full_width=True,
        allow_select_none=False,
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
    mo.stop(not dataset_tables, f"Schema '{selected_schema.value}' has no resource tables")
    selected_table = mo.ui.dropdown(
        options=dataset_tables,
        value=dataset_tables[0],
        label="Resource table",
        full_width=True,
        allow_select_none=False,
    )
    return dlt_dataset, selected_table


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Dataset preview
    Select a dataset and table to inspect the resource schema and show a preview of the collected resources
    """)
    return


@app.cell
def _(matched_extension_stat, mo, selected_schema, selected_table):
    context_message = (
        f"Inspecting `{selected_schema.value}.{selected_table.value}` with extension `{selected_schema.value}`."
        if selected_schema.value and selected_table.value
        else "Select a schema and table to inspect collected resources."
    )
    data_filters = mo.vstack(
        [
            mo.hstack(
                [selected_schema, selected_table, matched_extension_stat],
                gap=1,
                widths="equal",
            ),
            mo.md(context_message),
        ],
        gap=1,
    )
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
def _(dlt_dataset, dlt_pipeline, os, pl, selected_table):
    last_load_info = dlt_pipeline.last_trace.last_load_info.asdict()
    last_fs_destination = last_load_info["destination_displayable_credentials"]
    os.environ["BUCKET_URL"] = last_fs_destination
    dlt_table = dlt_dataset.table(table_name=selected_table.value)
    available_columns_df = pl.DataFrame(list(dlt_table.schema["columns"].values()))
    available_columns_df
    return (last_fs_destination,)


@app.cell
def _(Path, dlt_pipeline, last_fs_destination, pl, selected_table):
    dataset_path = (
        Path(last_fs_destination.replace("file://", ""))
        / dlt_pipeline.dataset_name
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
def _(collector_options, dlt_pipeline, mo, selected_schema):
    matched_extension_name = None
    extension_name = (
        selected_schema.value
        if selected_schema.value in collector_options
        else dlt_pipeline.dataset_name
    )

    if extension_name not in collector_options:
        matched_extension_stat = mo.callout(
            f"No loaded extension matches schema `{selected_schema.value}` or dataset `{dlt_pipeline.dataset_name}`.",
            kind="warn",
        )
    else:
        matched_extension_name = extension_name
        matched_extension_stat = mo.stat(
            value=matched_extension_name,
            label="Matched extension",
            caption="Matched from dataset schema",
        )
    return matched_extension_name, matched_extension_stat


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
def _(mo, sample_count):
    mo.hstack([sample_count], widths="equal")
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
def _(collector_options, matched_extension_name, mo):
    mo.stop(not matched_extension_name, "No loaded extension matches the selected schema")
    selected_extension = collector_options[matched_extension_name]
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
        if DEFAULT_LOOKUP_FILE.exists():
            lookup_client = duckdb.connect(str(DEFAULT_LOOKUP_FILE), read_only=True)
            lookup_session = selected_extension.lookup_factory(lookup_client)
        else:
            mo.callout(
                f"Lookup file `{DEFAULT_LOOKUP_FILE}` was not found. Node preview will still run, but lookup-backed properties may fail.",
                kind="warn",
            )
    return (lookup_session,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    #### As node
    """)
    return


@app.cell
def _(
    asdict,
    is_dataclass,
    lookup_session,
    mo,
    pl,
    sample_df,
    selected_table,
    table_to_asset,
):
    mo.stop(
        selected_table.value not in table_to_asset,
        f"Selected table '{selected_table.value}' is not mapped to an OpenHound asset",
    )

    def node_to_dict(node):
        if hasattr(node, "model_dump"):
            return node.model_dump(mode="json")
        if is_dataclass(node):
            return asdict(node)
        return dict(node)

    def as_node_preview(row, model):
        parsed_model = model.model_validate(row)
        parsed_model._lookup = lookup_session
        parsed_model._extras = {}
        node = parsed_model.as_node
        if node is None:
            return None

        node_dict = node_to_dict(node)
        properties = node_dict.pop("properties", {}) or {}
        return {**node_dict, **{f"prop_{key}": value for key, value in properties.items()}}

    node_preview_rows = [
        preview_row
        for preview_row in [
            as_node_preview(row, table_to_asset[selected_table.value])
            for row in sample_df.iter_rows(named=True)
        ]
        if preview_row is not None
    ]

    as_node_df = pl.DataFrame(node_preview_rows)
    as_node_df
    return

if __name__ == "__main__":
    app.run()
