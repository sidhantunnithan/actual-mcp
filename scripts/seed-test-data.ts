/**
 * Seeds an Actual Budget server with test data.
 *
 * If no budget exists on the server, creates one and uploads it.
 * If the budget doesn't have a "TESTING" account, creates one.
 * Outputs the budget sync ID (groupId) to stdout.
 */
import api from '@actual-app/api';
import fs from 'fs';
import os from 'os';
import path from 'path';

const SERVER_URL = process.env.ACTUAL_SERVER_URL || 'http://localhost:5006';
const PASSWORD = process.env.ACTUAL_PASSWORD || 'test-password';

type InternalSend = (method: string, args?: Record<string, unknown>) => Promise<Record<string, unknown>>;

async function seed(): Promise<void> {
  // Step 1: Bootstrap the server (set password on first run)
  const bootstrapRes = await fetch(`${SERVER_URL}/account/bootstrap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: PASSWORD }),
  });

  if (bootstrapRes.ok) {
    console.error('Server bootstrapped with password.');
  } else {
    const body = await bootstrapRes.text();
    if (body.includes('already-bootstrapped')) {
      console.error('Server already bootstrapped.');
    } else {
      console.error(`Bootstrap response (${bootstrapRes.status}): ${body}`);
    }
  }

  // Step 2: Initialize the API with a clean local data dir
  const dataDir = path.join(os.tmpdir(), 'actual-e2e-seed');
  if (fs.existsSync(dataDir)) {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
  fs.mkdirSync(dataDir, { recursive: true });
  // Reason: the API's internal exportDatabase() uses ACTUAL_DATA_DIR for temp backup path
  process.env.ACTUAL_DATA_DIR = dataDir;

  await api.init({ dataDir, serverURL: SERVER_URL, password: PASSWORD });
  console.error('API initialized.');

  const send = (api.internal as { send: InternalSend }).send;

  // Step 3: Check for existing budgets, create one if needed
  let budgets = await api.getBudgets();

  if (!budgets || budgets.length === 0) {
    console.error('No budgets found. Creating one...');

    // create-budget opens the budget locally after creation
    await send('create-budget', { budgetName: 'E2E Test Budget' });
    console.error('Budget created locally.');

    const uploadResult = await send('upload-budget');
    if (uploadResult.error) {
      throw new Error(`Upload failed: ${JSON.stringify(uploadResult.error)}`);
    }
    console.error('Budget uploaded to server.');

    // Close the budget so downloadBudget below works cleanly
    await send('close-budget');
    budgets = await api.getBudgets();
  }

  // Reason: downloadBudget() matches on groupId, not cloudFileId.
  // The "Sync ID" shown in Actual's Advanced settings IS the groupId.
  const budget = budgets.find((b) => 'groupId' in b && b.groupId) || budgets[0];
  const syncId = budget.groupId || budget.cloudFileId || budget.id || '';

  if (!syncId) {
    throw new Error('Could not determine budget sync ID.');
  }
  console.error(`Using budget sync ID: ${syncId}`);

  // Step 4: Download the budget and ensure the TESTING account exists
  await api.downloadBudget(syncId);
  console.error('Budget downloaded.');

  const accounts = await api.getAccounts();
  const hasTestingAccount = accounts.some((a) => a.name === 'TESTING');

  if (!hasTestingAccount) {
    await api.createAccount({ name: 'TESTING', offbudget: false }, 0);
    console.error('Created "TESTING" account.');
  } else {
    console.error('"TESTING" account already exists.');
  }

  await api.shutdown();

  // Output only the sync ID to stdout (consumed by the orchestrator)
  process.stdout.write(syncId);
}

seed().catch((err) => {
  console.error('Seed failed:', err);
  process.exit(1);
});
