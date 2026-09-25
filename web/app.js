const names = ['batch', 'balances', 'scenarios'];
const $ = (id) => document.getElementById(id);
let lastReport = null;

function status(message, error = false) {
  $('message').textContent = message;
  $('message').classList.toggle('error', error);
}

function cell(row, value, className = '') {
  const td = document.createElement('td');
  td.textContent = String(value);
  if (className) td.className = className;
  row.appendChild(td);
}

async function loadDemo() {
  status('Loading synthetic fixtures…');
  try {
    const response = await fetch('/demo');
    if (!response.ok) throw new Error(`Demo unavailable (${response.status})`);
    const data = await response.json();
    for (const name of names) $(name).value = data[name];
    $('expected-safe').value = '0x1111111111111111111111111111111111111111';
    status('Synthetic demo loaded. Click Run preflight.');
  } catch (error) {
    status(error.message, true);
  }
}

function render(report) {
  lastReport = report;
  $('results').classList.remove('hidden');
  $('worst-prefix').textContent = `${report.worst_case_funded_prefix} / ${report.calls.length}`;
  $('unknown-count').textContent = report.unclassified_indices.length;
  $('scenario-count').textContent = report.scenario_results.length;
  $('result-warning').textContent = report.all_modeled_calls_funded
    ? 'The modeled calls have enough stated balance. This does not prove execution safety, token behavior, or actual fee settlement.'
    : 'At least one scenario stops early. Never treat a funded prefix as approval to sign or execute.';
  $('batch-hash').textContent = `Batch SHA-256 ${report.batch_sha256}`;
  $('scenario-rows').replaceChildren();
  for (const item of report.scenario_results) {
    const row = document.createElement('tr');
    cell(row, item.id, 'bold');
    cell(row, `${(item.outflow_multiplier_bps / 100).toFixed(0)}%`);
    cell(row, item.native_fee_reserve_wei, 'mono');
    cell(row, `${item.funded_prefix} / ${report.calls.length}`, 'bold');
    cell(row, item.first_blocker ? `Call ${item.first_blocker.index + 1}: ${item.first_blocker.reason}` : 'No modeled blocker');
    $('scenario-rows').appendChild(row);
  }
  $('call-rows').replaceChildren();
  for (const item of report.calls) {
    const row = document.createElement('tr');
    cell(row, item.index + 1);
    cell(row, item.kind.replaceAll('_', ' '), item.kind === 'unclassified' ? 'caution' : 'bold');
    cell(row, item.asset || '—', 'mono');
    cell(row, item.amount_raw ?? '—', 'mono');
    cell(row, item.reason);
    $('call-rows').appendChild(row);
  }
  $('results').scrollIntoView({ behavior: 'smooth' });
}

async function analyze() {
  if (names.some((name) => !$(name).value.trim())) {
    status('Provide a batch, balances, and scenarios first.', true);
    return;
  }
  $('analyze').disabled = true;
  status('Running local preflight…');
  try {
    const request = Object.fromEntries(names.map((name) => [name, $(name).value]));
    request.expected_safe = $('expected-safe').value.trim() || null;
    const response = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Analysis failed (${response.status})`);
    render(data);
    status('Local preflight complete. No transaction was sent.');
  } catch (error) {
    lastReport = null;
    $('results').classList.add('hidden');
    status(error.message, true);
  } finally {
    $('analyze').disabled = false;
  }
}

function download() {
  if (!lastReport) return;
  const blob = new Blob([JSON.stringify(lastReport, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'safe-treasury-preflight-report.json';
  a.click();
  URL.revokeObjectURL(url);
}

for (const name of names) {
  $(`${name}-file`).addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1_000_000) {
      status(`${name} file exceeds 1 MB.`, true);
      return;
    }
    $(name).value = await file.text();
    status(`${file.name} loaded locally. No analysis ran yet.`);
  });
}
$('load-demo').addEventListener('click', loadDemo);
$('analyze').addEventListener('click', analyze);
$('download').addEventListener('click', download);
