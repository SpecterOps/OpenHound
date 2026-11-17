## Cloud collection beta examples

Internal reference for employees configuring managed AWS collection for select
BloodHound Enterprise (BHE) beta customers.

Use [job-params.jsonc](job-params.jsonc) as a BHE `params` example specifically for
the OpenHound AWS extension. Set
`member_role_source` based on which identity the member roles trust:

- **Strategy A — `source`:** use the management role for organization survey and
  management-account collection, and assume member roles directly from the
  starting key identity.
- **Strategy B — `management`:** assume the management role for organization
  survey and management-account collection, then assume member roles through
  that management identity.

**Remove the comments before using the contents as the job's `params`
object in BHE.** The example uses JSONC; BHE requires strict JSON.
It contains only params, not a complete enqueue request. Replace
the placeholder account ID and role names; the named IAM roles, permissions and
trust relationships must already exist. Both strategies use a management-role hop.

Keep raw credentials out of this document. OpenHound obtains the access key ID
from the claimed BHE job, retrieves the secret access key from Secrets Manager,
and supplies the pair through the worker environment.

OpenHound delivers these settings through DLT; OH-AWS defines and validates them
and handles authentication, organization survey, and collection. This JSON-only
beta does not support customer-provided AWS files on the collector host.

**The example may become outdated as OH-AWS changes.** Check the
[AWS authentication documentation](https://github.com/SpecterOps/openhound-aws/blob/main/docs/authentication.md)
for the deployed extension version.
