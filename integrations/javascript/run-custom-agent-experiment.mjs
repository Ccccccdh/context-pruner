#!/usr/bin/env node

import { ContextPrunerSidecarClient } from './context-pruner-sidecar-client.mjs';


function parseArgs(argv) {
  const values = {
    mode: 'mock',
    repeats: 1,
    model: 'deepseek-v4-flash',
    modelBaseUrl: 'https://api.deepseek.com',
    maxOutputTokens: 160,
    maxApiRetries: 2,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    const value = argv[index + 1];
    if (name === '--mode') values.mode = value;
    else if (name === '--repeats') values.repeats = Number(value);
    else if (name === '--model') values.model = value;
    else if (name === '--model-base-url') values.modelBaseUrl = value;
    else if (name === '--max-output-tokens') values.maxOutputTokens = Number(value);
    else if (name === '--max-api-retries') values.maxApiRetries = Number(value);
    else throw new Error(`Unknown argument: ${name}`);
    index += 1;
  }
  if (!['mock', 'api'].includes(values.mode)) throw new Error('--mode must be mock or api');
  if (!Number.isInteger(values.repeats) || values.repeats <= 0) throw new Error('--repeats must be positive');
  return values;
}


const TASKS = [
  {
    id: 'incident_decision',
    taskState: 'Apply the current incident rule and return the one-line decision.',
    system: 'Use only the latest verified dossier. Return only the requested one-line contract.',
    current: 'CURRENT DOSSIER: Incident INC-882 affects service Helios-4. Verified rollback_trigger=true and customer_impact=confirmed. Rule: when both fields are true, action is ROLLBACK; otherwise action is MONITOR. Return exactly: DECISION incident=INC-882 action=<action>',
    expected: ['INC-882', 'ROLLBACK'],
    mockOutput: 'DECISION incident=INC-882 action=ROLLBACK',
  },
  {
    id: 'release_gate',
    taskState: 'Evaluate the current release gates and return the one-line status.',
    system: 'Use only the latest verified release record. Return only the requested one-line contract.',
    current: 'CURRENT RELEASE: Vega-21 has unit_tests=passed, integration_tests=passed, and security_gate=failed. Rule: every required gate must pass; any failed required gate makes the release BLOCKED. Return exactly: RELEASE name=Vega-21 status=<status>',
    expected: ['Vega-21', 'BLOCKED'],
    mockOutput: 'RELEASE name=Vega-21 status=BLOCKED',
  },
  {
    id: 'migration_plan',
    taskState: 'Select the approved current migration target and window.',
    system: 'Use only the latest approved migration record. Return only the requested one-line contract.',
    current: 'CURRENT MIGRATION: Customer Polaris approved region=ap-southeast and window=03:30 UTC. The approved record overrides all draft alternatives. Return exactly: MIGRATION customer=Polaris region=<region> window=<window>',
    expected: ['Polaris', 'ap-southeast', '03:30 UTC'],
    mockOutput: 'MIGRATION customer=Polaris region=ap-southeast window=03:30 UTC',
  },
];


function buildHistory(task) {
  const messages = [{ role: 'system', content: task.system }];
  for (let index = 0; index < 14; index += 1) {
    messages.push({
      role: 'assistant',
      content: `OBSOLETE DRAFT ${index}: superseded identifiers, regions, windows, and actions. `.repeat(3),
    });
    messages.push({
      role: 'user',
      content: `ARCHIVED REQUEST ${index}: compare an abandoned option that is no longer authorized. `.repeat(3),
    });
  }
  messages.push({ role: 'user', content: task.current });
  return messages;
}


async function callModel(messages, options) {
  if (options.mode === 'mock') return null;
  const apiKey = String(process.env.MODEL_API_KEY || '');
  if (!apiKey) throw new Error('MODEL_API_KEY is required in api mode');
  const url = `${options.modelBaseUrl.replace(/\/$/, '')}/chat/completions`;
  let lastError;
  for (let attempt = 0; attempt <= options.maxApiRetries; attempt += 1) {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          model: options.model,
          messages,
          temperature: 0,
          max_tokens: options.maxOutputTokens,
          thinking: { type: 'disabled' },
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(String(data?.error?.message || `Model HTTP ${response.status}`));
        error.retryable = response.status === 429 || response.status >= 500;
        throw error;
      }
      return data;
    } catch (error) {
      lastError = error;
      const retryable = error?.retryable !== false;
      if (!retryable || attempt >= options.maxApiRetries) break;
      await new Promise((resolve) => setTimeout(resolve, 500 * (2 ** attempt)));
    }
  }
  throw lastError;
}


async function runLifecycleProbe(client) {
  const sourceId = 'javascript:lifecycle:source';
  const restoredId = 'javascript:lifecycle:restored';
  const messages = [{ role: 'system', content: 'Hard constraint: preserve JS-Omega-42.' }];
  for (let index = 0; index < 20; index += 1) {
    const id = String(index).padStart(2, '0');
    messages.push({
      role: 'assistant',
      content: `Archived JavaScript evidence ARCHIVE-JS-${id}: obsolete branch. `.repeat(3),
    });
    messages.push({
      role: 'user',
      content: `Old JavaScript request ${id}: inspect ARCHIVE-JS-${id}. `.repeat(3),
    });
  }
  messages.push({ role: 'user', content: 'Use fresh tool evidence for JS-Omega-42.' });
  const health = await client.health();
  const capabilities = await client.capabilities();
  await client.beforeModel(sourceId, {
    messages,
    task_state: 'Preserve JS-Omega-42 and fresh tool evidence.',
    method: 'pruner_v1',
    budget: { soft: 700, hard: 900, target: 520 },
    reserved_tokens: 120,
  });
  await client.afterTool(sourceId, {
    role: 'tool',
    tool_call_id: 'js-lookup-1',
    name: 'lookup_ticket',
    content: 'JS-TOOL-908 severity=critical project=JS-Omega-42',
  });
  await client.afterModel(sourceId, {
    role: 'assistant',
    content: 'Recorded JS-TOOL-908 for JS-Omega-42.',
  });
  const exported = await client.getState(sourceId);
  await client.restoreState(restoredId, exported.lifecycle_state, {
    taskState: 'Restored JavaScript lifecycle',
  });
  const recovered = await client.onError(restoredId, 'synthetic JavaScript retry', {
    query: 'ARCHIVE-JS-07',
    recover: true,
  });
  const finalized = await client.finalize(restoredId, {
    host: 'custom-javascript-agent',
    validation: 'full-client-lifecycle',
  });
  const serialized = JSON.stringify(finalized.lifecycle_state || {});
  const sourceDelete = await client.deleteSession(sourceId);
  const restoredDelete = await client.deleteSession(restoredId);
  const metrics = finalized.metrics || {};
  const probe = {
    success:
      health?.status === 'ok'
      && Boolean(capabilities?.routes)
      && Boolean(finalized.lifecycle_state?.plugin?.finalized)
      && Number(metrics.lifecycle_recovery_count || 0) >= 1
      && serialized.includes('JS-TOOL-908')
      && serialized.includes('JS-Omega-42')
      && sourceDelete?.deleted === true
      && restoredDelete?.deleted === true,
    health_ok: health?.status === 'ok',
    capabilities_ok: Boolean(capabilities?.routes),
    finalized: Boolean(finalized.lifecycle_state?.plugin?.finalized),
    recovery_count: Number(metrics.lifecycle_recovery_count || 0),
    tool_fact_preserved: serialized.includes('JS-TOOL-908'),
    constraint_preserved: serialized.includes('JS-Omega-42'),
    sessions_deleted: sourceDelete?.deleted === true && restoredDelete?.deleted === true,
    recovery_view_contains_query: JSON.stringify(recovered.messages || []).includes('ARCHIVE-JS-07'),
  };
  return probe;
}


async function run() {
  const options = parseArgs(process.argv.slice(2));
  const sidecarUrl = String(process.env.CONTEXT_PRUNER_BASE_URL || '');
  const sidecarToken = String(process.env.CONTEXT_PRUNER_AUTH_TOKEN || '');
  const client = new ContextPrunerSidecarClient({
    baseUrl: sidecarUrl,
    authToken: sidecarToken,
    timeoutMs: 60_000,
  });
  const rows = [];
  for (let repeat = 0; repeat < options.repeats; repeat += 1) {
    for (const task of TASKS) {
      for (const method of ['none', 'pruner_v1']) {
        const sessionId = `javascript:${task.id}:r${repeat}:${method}`;
        const fullHistory = buildHistory(task);
        const before = await client.beforeModel(sessionId, {
          messages: fullHistory,
          task_state: task.taskState,
          method,
          budget: { soft: 900, hard: 1200, target: 650 },
          reserved_tokens: 180,
        });
        const completion = await callModel(before.messages, options);
        const output = options.mode === 'mock'
          ? task.mockOutput
          : String(completion?.choices?.[0]?.message?.content || '').trim();
        await client.afterModel(sessionId, { role: 'assistant', content: output });
        const finalized = await client.finalize(sessionId, {
          host: 'custom-javascript-agent',
          mode: options.mode,
        });
        const normalized = output.toLowerCase();
        const missing = task.expected.filter((term) => !normalized.includes(term.toLowerCase()));
        const usage = completion?.usage || {};
        const metrics = finalized.metrics || {};
        rows.push({
          task_id: task.id,
          repeat,
          method,
          session_id: sessionId,
          success: output.length > 0 && missing.length === 0,
          missing_terms: missing,
          model_output: output,
          prompt_tokens: Number(usage.prompt_tokens || 0),
          completion_tokens: Number(usage.completion_tokens || 0),
          full_context_tokens: Number(metrics.last_full_context_tokens || 0),
          served_context_tokens: Number(metrics.last_served_context_tokens || 0),
          model_input_budget_violation_count: Number(metrics.model_input_budget_violation_count || 0),
          finalized: Boolean(finalized.lifecycle_state?.plugin?.finalized),
        });
      }
    }
  }
  const lifecycleProbe = await runLifecycleProbe(client);
  process.stdout.write(`${JSON.stringify({ mode: options.mode, rows, lifecycle_probe: lifecycleProbe })}\n`);
}


run().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
