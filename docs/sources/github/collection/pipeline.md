# Pipeline

This page lists all pipeline functions that make up the `github` collector.

## Sources
Sources are the top-level entry points that define the configuration and which resources/transformers to process.

This section includes the API reference for the source function, including  all parameters required to run the pipeline. The parameters may also include references to credentials or settings stored inside your DLT configuration, which can be loaded via environment variables or the .dlt/secrets.toml file.

!!! info "Configuration Required"
    Some source parameters may require configuration values that should be set via:

    - **Environment variables** (recommended for secrets);
    - **`.dlt/config.toml`** for non-sensitive configuratio;n
    - **`.dlt/secrets.toml`** for API keys, tokens, and credentials

    See the [DLT credentials documentation](https://dlthub.com/docs/walkthroughs/add_credentials) for details on managing credentials.



::: openhound_github.source.source
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3



## Resources

Resources are the individual extraction functions which are part of a DLT pipeline. Within the context of OpenHound, resources are functions that call individual API endpoints to fetch users, computers, roles etc. Each `@app.resource` represents a specific data item that can be extracted and loaded.

**As part of this collector:**

- Some resources return OpenGraph assets that can be loaded directly into BloodHound. These are wrapped with the @app.asset decorator;
- Other resources provide supplementary data used for enrichment, transformations or other supporting operations


::: openhound_github.source.actions_permissions
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.app_installations
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.external_identities
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.org_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.organization_secrets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.organization_variables
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.organizations
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.personal_access_token_requests
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.personal_access_tokens
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repositories
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repositories_graphql
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repository_roles_base
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.saml_provider
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.scim_users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.secret_scanning_alerts
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.teams
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.users
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3



## Transformers

Transformers are similar to resources but are used for data transformation and enrichment. They typically receive the output of a resource and yield additional records. An example could be fetching user details via a seperate API endpoint based on a user ID returned by a resource.


::: openhound_github.source.applications
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.branch_protection_rules
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.branches
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.environment_branch_policies
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.environment_secrets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.environment_variables
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.environments
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.org_role_members
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.org_role_teams
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.pat_repo_access
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repo_role_assignments
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repository_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repository_secrets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.repository_variables
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.selected_organization_secrets
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.selected_organization_variables
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.team_members
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.team_roles
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3
::: openhound_github.source.workflows
    options:
        show_source_link: true
        show_root_heading: true
        heading_level: 3

