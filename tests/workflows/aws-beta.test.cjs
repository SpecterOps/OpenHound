const assert = require('node:assert/strict');
const { test } = require('node:test');
const { prepareBuild, authorizeActors } = require('../../.github/scripts/aws-beta.cjs');

function preparation() {
  const outputs = {};
  const environment = {
    can_admins_bypass: false,
    deployment_branch_policy: { custom_branch_policies: true },
    protection_rules: [{
      type: 'required_reviewers', prevent_self_review: true,
      reviewers: [{ type: 'Team', reviewer: { slug: 'openhound' } }],
    }],
  };
  const policies = { total_count: 1, branch_policies: [{ name: 'main', type: 'branch' }] };
  const summary = { addHeading() { return this; }, addTable() { return this; }, async write() {} };
  return {
    environment, policies, outputs,
    context: {
      repo: { owner: 'SpecterOps', repo: 'OpenHound' }, ref: 'refs/heads/main',
      sha: 'a'.repeat(40), payload: { repository: { default_branch: 'main' } },
    },
    env: {
      VERSION: '0.4.1.dev9346005', SOURCE_REF: 'main',
      ECR_REGISTRY: '590372943691.dkr.ecr.us-east-1.amazonaws.com', ECR_REPOSITORY: 'bhe/openhound',
    },
    core: { setOutput(name, value) { outputs[name] = value; }, summary },
    github: { rest: { repos: {
      async getEnvironment() { return { data: environment }; },
      async listDeploymentBranchPolicies() { return { data: policies }; },
      async getBranch() { return { data: { commit: { sha: 'b'.repeat(40) } } }; },
    } } },
  };
}

test('main builds preserve the required image format and pin both source repositories', async () => {
  const input = preparation();
  await prepareBuild(input);
  assert.equal(input.outputs.image_tag, 'ecr-enterprise0.4.1.dev9346005');
  assert.equal(input.outputs.source_sha, 'b'.repeat(40));
  assert.match(input.outputs.aws_sha, /^[0-9a-f]{40}$/);
});

test('only the explicitly approved beta source branch is accepted', async () => {
  const input = preparation();
  input.env.SOURCE_REF = 'feat/beta';
  await assert.rejects(prepareBuild(input), /Source must be/);
  input.env.APPROVED_BETA_BRANCH = 'feat/beta';
  await prepareBuild(input);
});

test('feature-branch workflow revisions cannot publish', async () => {
  const input = preparation();
  input.context.ref = 'refs/heads/feat/beta';
  await assert.rejects(prepareBuild(input), /default branch/);
});

test('release tags, prefixed tags, and shell expressions are rejected', async () => {
  for (const version of ['0.4.1', 'v0.4.1.dev5', 'ecr-enterprise0.4.1.dev5', '0.4.1.dev5$(id)', '0.4.1.dev5\n']) {
    const input = preparation();
    input.env.VERSION = version;
    await assert.rejects(prepareBuild(input), /development version/);
  }
});

test('missing or weakened environment approvals fail closed', async () => {
  for (const weaken of [
    input => { input.environment.can_admins_bypass = true; },
    input => { input.environment.protection_rules = []; },
    input => { input.environment.protection_rules[0].prevent_self_review = false; },
    input => { input.environment.protection_rules[0].reviewers.push({ type: 'User' }); },
    input => { input.environment.protection_rules[0].reviewers[0].reviewer.slug = 'other'; },
    input => { input.policies.branch_policies[0].name = '*'; },
    input => { input.policies.branch_policies[0].type = 'tag'; },
    input => { input.policies.total_count = 2; },
  ]) {
    const input = preparation();
    weaken(input);
    await assert.rejects(prepareBuild(input), /managed-aws-beta/);
  }
});

function authorization(states = {}) {
  const checked = [];
  return {
    checked,
    context: { actor: 'owner', repo: { owner: 'SpecterOps' } },
    env: { AUTHORIZATION_TOKEN: 'test-only', TRIGGERING_ACTOR: 'rerunner' },
    github: { rest: { teams: {
      async getMembershipForUserInOrg({ username, org, team_slug }) {
        assert.equal(org, 'SpecterOps');
        assert.equal(team_slug, 'openhound');
        checked.push(username);
        if (states[username] instanceof Error) throw states[username];
        return { data: { state: states[username] || 'active' } };
      },
    } } },
  };
}

test('both the original actor and rerun actor must be active maintainers', async () => {
  const input = authorization();
  await authorizeActors(input);
  assert.deepEqual(input.checked, ['owner', 'rerunner']);
  for (const states of [{ owner: 'pending' }, { rerunner: 'pending' }, { rerunner: new Error('404') }]) {
    await assert.rejects(authorizeActors(authorization(states)));
  }
});

test('missing membership credentials or rerun identity fail closed', async () => {
  const input = authorization();
  input.env.AUTHORIZATION_TOKEN = '';
  await assert.rejects(authorizeActors(input), /AUTHORIZATION_TOKEN/);
  input.env.AUTHORIZATION_TOKEN = 'test-only';
  input.env.TRIGGERING_ACTOR = '';
  await assert.rejects(authorizeActors(input), /triggering actor/);
});
