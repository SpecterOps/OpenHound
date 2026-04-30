# Collecting resources
This page explains how OpenHound uses `dlt` sources and resources to collect and transform data. A DLT source groups resources and declares configuration items (like secrets), while a resource yield assets (eg. users/computers/roles etc) that are stored in JSONL/Parquet format as part of the pipeline. If you followed the [Creating a new collector](creating-collector.md) process, the cookiecutter template already generates an example source and resource for you that you can modify.

## DLT source
The `@app.source` function is the starting point of a collector. It receives/parses configuration, builds shared clients or context and returns DLT resources or DLT transformers. The source decides which resources are part of the collection. By default, running the pipeline (ie. using the `collect` cli command) will collect all resources returned by this function, though a subset of resources can be selected as part of the CLI options.

The source is defined in `source.py` (SuperIAM is the sample name for a custom service):

```py
@app.source(name="SuperIAM", max_table_nesting=0)
def source(token=dlt.secrets.value, host=dlt.secrets.value):
    ctx = SourceContext(
        client=RESTClient(
            base_url=host,
            headers={"accept": "application/json"},
            auth=BearerTokenAuth(token=token),
            paginator=SinglePagePaginator(),
        )
    )

    return (users(ctx),)
```

**Key points:**

- `token` and `host` are read from `dlt` secrets so they are not hard-coded. These can either be read from your environment or stored inside a .dlt/secrets.toml and/or dlt/config.toml config file.
- A shared `SourceContext` containing a global request/Rest client is created and passed to all resources.
- The source returns a tuple of resources (in this case, only: `users`).


## DLT resource
The `@app.resource` function is a wrapper for a DLT resource (@dlt.resource) and yields assets (eg. users/computers/roles etc) to store on disk. In this case, we use DLT's included RESTClient, which automatically handles pagination, retries and rate-limiting. Compared to the original dlt resource, additional exception handling strategies can be added to continue the pipeline in case a single resource fails.

The template includes a simple resource:

```py
@app.resource(name="users", parallelized=True, columns=User)
def users(ctx: SourceContext):
    response = ctx.client.get("/users").json()
    # Option A:
    # Yield individual users by iterating over the response
    for user in response["users"]:
        yield user

    # Option B:
    # Or return the list as is if modifications are
    # not needed
    yield response["users"]
```

**Key Points:**

- The resource uses RESTClient as part of the shared context `ctx` to fetch users via the `/users` endpoint.
- `yield` returns one "row" ie. user at a time.
- `name="users"` becomes the "table" name by default, in this case the directory name where the files will be stored on disk eg. SuperIAM/users/data.jsonl.gz
- `parallelized=True` allows the resource to run concurrently. The concurrency limits can be set via the .dlt/config.toml configuration file.


## Extending the source
This structure keeps your collectors clean and easy to extend. To collect another asset, create a new `@app.resource` function and add it to the returned resources as part of your source.

### Example: adding a second resource

```py
@app.resource(name="computers", columns=Computer)
def computers(ctx: SourceContext):
    response = ctx.client.get("/computers").json()
    yield response["computers"]


@app.resource(name="users", parallelized=True, columns=User)
def users(ctx: SourceContext):
    response = ctx.client.get("/users").json()
    yield response["users"]

@app.source(name="SuperIAM", max_table_nesting=0)
def source(token=dlt.secrets.value, host=dlt.secrets.value):
    ctx = SourceContext(
        client=RESTClient(
            base_url=host,
            headers={"accept": "application/json"},
            auth=BearerTokenAuth(token=token),
            paginator=SinglePagePaginator(),
        )
    )

    return (users(ctx), computers(ctx))
```

That is all for at least the basics of collection. You may have noticed that each resource has a 'columns=' argument, pointing to a particular class. These are custom classess that validates data returned by the resource and provide a schema for the OpenGraph node/edge representation. Next up is defining these [resource models](modelling.md).
