# Pipeline

This page lists all pipeline functions that make up the `jamf` collector.

## Sources
Sources are the top-level entry points that define the configuration and which resources/transformers to process.

This section includes the API reference for the source function, including  all parameters required to run the pipeline. The parameters may also include references to credentials or settings stored inside your DLT configuration, which can be loaded via environment variables or the .dlt/secrets.toml file.

!!! info "Configuration Required"
    Some source parameters may require configuration values that should be set via:

    - **Environment variables** (recommended for secrets);
    - **`.dlt/config.toml`** for non-sensitive configuratio;n
    - **`.dlt/secrets.toml`** for API keys, tokens, and credentials

    See the [DLT credentials documentation](https://dlthub.com/docs/walkthroughs/add_credentials) for details on managing credentials.



*No source functions found.*


## Resources

Resources are the individual extraction functions which are part of a DLT pipeline. Within the context of OpenHound, resources are functions that call individual API endpoints to fetch users, computers, roles etc. Each `@app.resource` represents a specific data item that can be extracted and loaded.

**As part of this collector:**

- Some resources return OpenGraph assets that can be loaded directly into BloodHound. These are wrapped with the @app.asset decorator;
- Other resources provide supplementary data used for enrichment, transformations or other supporting operations


::: openhound_jamf.source.account_groups
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.accounts
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.api_integrations
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.api_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.computerextensionattributes
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.computers
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.policies
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.scripts
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.sites
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.sso
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.tenant
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3



## Transformers

Transformers are similar to resources but are used for data transformation and enrichment. They typically receive the output of a resource and yield additional records. An example could be fetching user details via a seperate API endpoint based on a user ID returned by a resource.


::: openhound_jamf.source.account_details
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.account_group_details
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.policy_details
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.script_details
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_jamf.source.user_details
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3

