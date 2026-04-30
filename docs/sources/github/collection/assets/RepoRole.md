# RepoRole
This section describes the exported OpenGraph asset(s) for the RepoRole class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | :fontawesome-solid-user-tie: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ReadRepoContents](../../graph/edges/GH_ReadRepoContents.md) | Role can read repo contents |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_WriteRepoContents](../../graph/edges/GH_WriteRepoContents.md) | Role can write repo contents |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_WriteRepoPullRequests](../../graph/edges/GH_WriteRepoPullRequests.md) | Role can write pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_AdminTo](../../graph/edges/GH_AdminTo.md) | Role has admin access to repo |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_HasBaseRole](../../graph/edges/GH_HasBaseRole.md) | Role inherits from base role |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_BypassBranchProtection](../../graph/edges/GH_BypassBranchProtection.md) | Role can bypass branch protection rules |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_PushProtectedBranch](../../graph/edges/GH_PushProtectedBranch.md) | Role can push to protected branches |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditRepoProtections](../../graph/edges/GH_EditRepoProtections.md) | Role can edit repository branch protection settings |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ViewSecretScanningAlerts](../../graph/edges/GH_ViewSecretScanningAlerts.md) | Role can view secret scanning alerts |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ResolveSecretScanningAlerts](../../graph/edges/GH_ResolveSecretScanningAlerts.md) | Role can resolve secret scanning alerts |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_DeleteAlertsCodeScanning](../../graph/edges/GH_DeleteAlertsCodeScanning.md) | Role can delete code scanning alerts |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RunOrgMigration](../../graph/edges/GH_RunOrgMigration.md) | Role can run organization migrations on the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageSecurityProducts](../../graph/edges/GH_ManageSecurityProducts.md) | Role can manage security products for the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageRepoSecurityProducts](../../graph/edges/GH_ManageRepoSecurityProducts.md) | Role can manage repository-level security products |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageWebhooks](../../graph/edges/GH_ManageWebhooks.md) | Role can manage repository webhooks |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageDeployKeys](../../graph/edges/GH_ManageDeployKeys.md) | Role can manage repository deploy keys |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CanCreateBranch](../../graph/edges/GH_CanCreateBranch.md) | Role can create new branches in the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_CanWriteBranch](../../graph/edges/GH_CanWriteBranch.md) | Role can push commits to this branch |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_CanEditProtection](../../graph/edges/GH_CanEditProtection.md) | Role can modify or remove the branch protection rule governing this branch |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ReadCodeScanning](../../graph/edges/GH_ReadCodeScanning.md) | Role can read code scanning results |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_WriteCodeScanning](../../graph/edges/GH_WriteCodeScanning.md) | Role can write code scanning results |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ViewDependabotAlerts](../../graph/edges/GH_ViewDependabotAlerts.md) | Role can view Dependabot alerts |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ResolveDependabotAlerts](../../graph/edges/GH_ResolveDependabotAlerts.md) | Role can resolve Dependabot alerts |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageTopics](../../graph/edges/GH_ManageTopics.md) | Role can manage repository topics |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageSettingsWiki](../../graph/edges/GH_ManageSettingsWiki.md) | Role can manage wiki settings |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageSettingsProjects](../../graph/edges/GH_ManageSettingsProjects.md) | Role can manage projects settings |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageSettingsMergeTypes](../../graph/edges/GH_ManageSettingsMergeTypes.md) | Role can manage merge type settings |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageSettingsPages](../../graph/edges/GH_ManageSettingsPages.md) | Role can manage GitHub Pages settings |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditRepoMetadata](../../graph/edges/GH_EditRepoMetadata.md) | Role can edit repository metadata (name, description, etc.) |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_SetInteractionLimits](../../graph/edges/GH_SetInteractionLimits.md) | Role can set interaction limits on the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_SetSocialPreview](../../graph/edges/GH_SetSocialPreview.md) | Role can set the repository social preview image |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditRepoAnnouncementBanners](../../graph/edges/GH_EditRepoAnnouncementBanners.md) | Role can edit repository announcement banners |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditRepoCustomPropertiesValues](../../graph/edges/GH_EditRepoCustomPropertiesValues.md) | Role can edit custom property values on the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CreateTag](../../graph/edges/GH_CreateTag.md) | Role can create tags in the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_DeleteTag](../../graph/edges/GH_DeleteTag.md) | Role can delete tags in the repository |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_JumpMergeQueue](../../graph/edges/GH_JumpMergeQueue.md) | Role can jump the merge queue |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CreateSoloMergeQueueEntry](../../graph/edges/GH_CreateSoloMergeQueueEntry.md) | Role can create a solo merge queue entry |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_AddLabel](../../graph/edges/GH_AddLabel.md) | Role can add labels to issues and pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RemoveLabel](../../graph/edges/GH_RemoveLabel.md) | Role can remove labels from issues and pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CloseIssue](../../graph/edges/GH_CloseIssue.md) | Role can close issues |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ReopenIssue](../../graph/edges/GH_ReopenIssue.md) | Role can reopen issues |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_DeleteIssue](../../graph/edges/GH_DeleteIssue.md) | Role can delete issues |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ClosePullRequest](../../graph/edges/GH_ClosePullRequest.md) | Role can close pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ReopenPullRequest](../../graph/edges/GH_ReopenPullRequest.md) | Role can reopen pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_AddAssignee](../../graph/edges/GH_AddAssignee.md) | Role can add assignees to issues and pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RemoveAssignee](../../graph/edges/GH_RemoveAssignee.md) | Role can remove assignees from issues and pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RequestPrReview](../../graph/edges/GH_RequestPrReview.md) | Role can request pull request reviews |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_MarkAsDuplicate](../../graph/edges/GH_MarkAsDuplicate.md) | Role can mark issues or pull requests as duplicates |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_SetMilestone](../../graph/edges/GH_SetMilestone.md) | Role can set milestones on issues and pull requests |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_SetIssueType](../../graph/edges/GH_SetIssueType.md) | Role can set issue types |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_DeleteDiscussion](../../graph/edges/GH_DeleteDiscussion.md) | Role can delete discussions |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ToggleDiscussionAnswer](../../graph/edges/GH_ToggleDiscussionAnswer.md) | Role can toggle the accepted answer on a discussion |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ToggleDiscussionCommentMinimize](../../graph/edges/GH_ToggleDiscussionCommentMinimize.md) | Role can minimize or un-minimize discussion comments |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CreateDiscussionCategory](../../graph/edges/GH_CreateDiscussionCategory.md) | Role can create discussion categories |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditDiscussionCategory](../../graph/edges/GH_EditDiscussionCategory.md) | Role can edit discussion categories |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ConvertIssuesToDiscussions](../../graph/edges/GH_ConvertIssuesToDiscussions.md) | Role can convert issues to discussions |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_CloseDiscussion](../../graph/edges/GH_CloseDiscussion.md) | Role can close discussions |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ReopenDiscussion](../../graph/edges/GH_ReopenDiscussion.md) | Role can reopen discussions |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditCategoryOnDiscussion](../../graph/edges/GH_EditCategoryOnDiscussion.md) | Role can edit the category on a discussion |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_ManageDiscussionBadges](../../graph/edges/GH_ManageDiscussionBadges.md) | Role can manage discussion badges |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_EditDiscussionComment](../../graph/edges/GH_EditDiscussionComment.md) | Role can edit discussion comments |
| [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_DeleteDiscussionComment](../../graph/edges/GH_DeleteDiscussionComment.md) | Role can delete discussion comments |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

