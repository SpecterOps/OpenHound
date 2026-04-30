# Pipeline

This page lists all pipeline functions that make up the `okta` collector.

## Sources
Sources are the top-level entry points that define the configuration and which resources/transformers to process.

This section includes the API reference for the source function, including  all parameters required to run the pipeline. The parameters may also include references to credentials or settings stored inside your DLT configuration, which can be loaded via environment variables or the .dlt/secrets.toml file.

!!! info "Configuration Required"
    Some source parameters may require configuration values that should be set via:

    - **Environment variables** (recommended for secrets);
    - **`.dlt/config.toml`** for non-sensitive configuratio;n
    - **`.dlt/secrets.toml`** for API keys, tokens, and credentials

    See the [DLT credentials documentation](https://dlthub.com/docs/walkthroughs/add_credentials) for details on managing credentials.



::: openhound_okta.source.source
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3



## Resources

Resources are the individual extraction functions which are part of a DLT pipeline. Within the context of OpenHound, resources are functions that call individual API endpoints to fetch users, computers, roles etc. Each `@app.resource` represents a specific data item that can be extracted and loaded.

**As part of this collector:**

- Some resources return OpenGraph assets that can be loaded directly into BloodHound. These are wrapped with the @app.asset decorator;
- Other resources provide supplementary data used for enrichment, transformations or other supporting operations


::: openhound_okta.source.agent_pools
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.api_services
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.api_tokens
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.applications
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.authorization_servers
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.built_in_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.client_applications
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.custom_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.devices
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.groups
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.identity_providers
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.organization
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.policy_types
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.realms
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.resource_sets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.user_role_assignments
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3



## Transformers

Transformers are similar to resources but are used for data transformation and enrichment. They typically receive the output of a resource and yield additional records. An example could be fetching user details via a seperate API endpoint based on a user ID returned by a resource.


::: openhound_okta.source.agents
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.application_group_push_mappings
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.application_jwks
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.application_secrets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.application_users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.built_in_role_permissions
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.client_role_assignments
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.custom_role_permissions
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.group_assigned_apps
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.group_memberships
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.group_role_assignments
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.identity_provider_users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.policies
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.policy_mappings
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_okta.source.resources
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3

