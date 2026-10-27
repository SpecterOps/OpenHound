const { readFileSync } = require('node:fs');

const environmentName = 'managed-aws-beta';
const maintainerTeam = 'openhound';

async function prepareBuild({ github, context, core, env }) {
  const defaultBranch = context.payload.repository.default_branch;
  if (context.repo.owner !== 'SpecterOps' || context.repo.repo !== 'OpenHound' ||
      context.ref !== `refs/heads/${defaultBranch}`) {
    throw new Error('Run this workflow from the SpecterOps/OpenHound default branch.');
  }
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.dev\d+$/.test(env.VERSION)) {
    throw new Error('Use a development version such as 0.4.1.dev9346005, without a tag prefix.');
  }
  const imageTag = `ecr-enterprise${env.VERSION}`;
  if (imageTag.length > 128) {
    throw new Error('The resulting ECR image tag exceeds 128 characters.');
  }
  if (env.SOURCE_REF !== defaultBranch &&
      (!env.APPROVED_BETA_BRANCH || env.SOURCE_REF !== env.APPROVED_BETA_BRANCH)) {
    throw new Error('Source must be the default branch or MANAGED_AWS_BETA_SOURCE_BRANCH.');
  }
  if (!/^\d{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com$/.test(env.ECR_REGISTRY) ||
      !/^[a-z0-9]+(?:[._/-][a-z0-9]+)*$/.test(env.ECR_REPOSITORY)) {
    throw new Error('Configure the existing us-east-1 ECR_REGISTRY and ECR_REPOSITORY variables.');
  }

  const { data: environment } = await github.rest.repos.getEnvironment({
    ...context.repo, environment_name: environmentName,
  });
  const reviewers = environment.protection_rules.find(rule => rule.type === 'required_reviewers');
  if (environment.can_admins_bypass !== false || !reviewers?.prevent_self_review ||
      reviewers.reviewers.length !== 1 || reviewers.reviewers[0].type !== 'Team' ||
      reviewers.reviewers[0].reviewer.slug !== maintainerTeam ||
      !environment.deployment_branch_policy?.custom_branch_policies) {
    throw new Error('Configure managed-aws-beta with only the openhound team as reviewers, ' +
      'no self-approval, no admin bypass, and a default-branch-only deployment policy.');
  }
  const { data: policies } = await github.rest.repos.listDeploymentBranchPolicies({
    ...context.repo, environment_name: environmentName, per_page: 100,
  });
  if (policies.total_count !== 1 || policies.branch_policies[0].name !== defaultBranch ||
      policies.branch_policies[0].type !== 'branch') {
    throw new Error('The managed-aws-beta environment must allow only the default branch.');
  }

  const { data: branch } = await github.rest.repos.getBranch({
    ...context.repo, branch: env.SOURCE_REF,
  });
  const dockerfile = readFileSync('Dockerfile.aws-bundle', 'utf8');
  const pins = [...dockerfile.matchAll(/^ARG OPENHOUND_AWS_COMMIT=([0-9a-f]{40})$/gm)];
  if (pins.length !== 1) {
    throw new Error('Dockerfile.aws-bundle must contain one full, pinned AWS commit SHA.');
  }
  const awsSha = pins[0][1];
  core.setOutput('source_sha', branch.commit.sha);
  core.setOutput('aws_sha', awsSha);
  core.setOutput('image_tag', imageTag);
  core.setOutput('ecr_registry', env.ECR_REGISTRY);
  core.setOutput('ecr_repository', env.ECR_REPOSITORY);
  await core.summary
    .addHeading('Managed AWS beta build awaiting owner approval')
    .addTable([
      [{ data: 'Item', header: true }, { data: 'Value', header: true }],
      ['Image', `${env.ECR_REGISTRY}/${env.ECR_REPOSITORY}:${imageTag}`],
      ['OpenHound commit', branch.commit.sha],
      ['AWS extension commit', awsSha],
      ['Build tooling commit', context.sha],
    ])
    .write();
}

async function authorizeActors({ github, context, env }) {
  if (!env.AUTHORIZATION_TOKEN) {
    throw new Error('Configure OPENHOUND_BETA_AUTHORIZATION_TOKEN with organization Members: read.');
  }
  if (!env.TRIGGERING_ACTOR) {
    throw new Error('The triggering actor is required, including for reruns.');
  }
  for (const username of new Set([context.actor, env.TRIGGERING_ACTOR])) {
    const { data: membership } = await github.rest.teams.getMembershipForUserInOrg({
      org: context.repo.owner, team_slug: maintainerTeam, username,
    });
    if (membership.state !== 'active') {
      throw new Error(`${username} must be an active member of ${context.repo.owner}/${maintainerTeam}.`);
    }
  }
}

module.exports = { prepareBuild, authorizeActors };
