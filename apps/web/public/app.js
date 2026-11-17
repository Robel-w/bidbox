'use strict';

const $ = id => document.getElementById(id);
const icons = {
  'arrow-right': ['M5 12h14', 'm12 5 7 7-7 7'],
  'arrow-left': ['M19 12H5', 'm12 5-7 7 7 7'],
  'arrow-up-right': ['M7 17 17 7', 'M7 7h10v10'],
  'chevron-down': ['m6 9 6 6 6-6'],
  'chevron-right': ['m9 6 6 6-6 6'],
  check: ['m5 12 4 4L19 6'],
  clock: ['M12 8v4l3 2', 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0'],
  shield: ['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z', 'm8 12 3 3 5-5'],
  search: ['M21 21l-5-5', 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
  link: ['M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2', 'M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2'],
  file: ['M14 2H6v20h12V6z', 'M14 2v5h5', 'M9 12h6', 'M9 16h6'],
  code: ['m8 7-5 5 5 5', 'm16 7 5 5-5 5'],
  alert: ['M12 3 2 21h20z', 'M12 9v5', 'M12 17h.01'],
  minus: ['M6 12h12'],
  replay: ['M3 10a9 9 0 1 1 2 9', 'M3 3v7h7'],
  flask: ['M9 3h6', 'M10 3v7L4 20h16l-6-10V3', 'M8 15h8'],
  connection: ['M8 12h8', 'M7 8a4 4 0 0 0 0 8', 'M17 8a4 4 0 0 1 0 8'],
};
let runs = [], published = [], initialPublished = [], current = null, data = null, screen = 'ask', source = null;
let routeVersion = 0, shownRuns = 4, searchResults = null, pendingDecision = null;
let refreshBusy = false, refreshQueued = false, refreshTimer = null;

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined && value !== null) element.textContent = String(value);
  return element;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.7');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  (icons[name] || icons.file).forEach(value => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', value);
    svg.append(path);
  });
  return svg;
}
function hydrateIcons() {
  document.querySelectorAll('[data-icon]').forEach(element => element.replaceChildren(icon(element.dataset.icon)));
}
function chip(label, kind = '', symbol = 'clock') {
  const element = node('span', `status-chip ${kind}`);
  element.append(icon(symbol), node('span', '', label));
  return element;
}
function routeLink(label, destination, className = 'button primary', symbol = 'arrow-right') {
  const link = node('a', className, label);
  link.href = destination;
  if (symbol) link.append(icon(symbol));
  return link;
}
function stamp(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time not recorded';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
function number(value) {
  return typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat().format(value) : 'Not reported';
}
function amount(value, currency) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Amount not recorded';
  const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
  return `${currency || 'Currency not recorded'} ${compact}`;
}
function cost(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(8)}` : 'Not configured';
}
function notice(message, kind = '') {
  const element = $('page-notice');
  element.replaceChildren(icon(kind === 'review' ? 'alert' : 'connection'), node('p', '', message));
  element.className = `notice ${kind === 'review' ? 'review-notice' : ''}`;
  element.hidden = !message;
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    if (!response.ok) {
      let message = response.status === 404 ? 'That recorded review was not found. Check the link or return to all reviews.' : 'Could not complete this request. Please try again.';
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') message = body.detail;
        else if (response.status === 422) message = 'Check your name, committee role, reason and independent-review confirmation.';
      } catch (_) {}
      throw new Error(message);
    }
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The connection is taking too long. No result has been confirmed; check the recorded status before trying again.');
    if (error instanceof TypeError) throw new Error('Couldn’t reach the service. Check your connection and try again.');
    throw error;
  } finally { clearTimeout(timer); }
}
function status(run) {
  if (run.status === 'awaiting_approval') return ['Needs a decision', 'review', 'clock'];
  if (run.status === 'rejected') return ['Rejected', 'review', 'minus'];
  if (run.file_created) return ['File created', 'ok', 'check'];
  if (run.status === 'approved') return ['Decision recorded', 'ok', 'check'];
  return ['Recorded progress', '', 'replay'];
}
function badges(id, detail, includeState = true) {
  const element = $(id);
  const origin = detail.origin;
  element.replaceChildren();
  if (origin === 'synthetic' || origin === 'mixed') element.append(chip(origin === 'mixed' ? 'SANDBOX: includes synthetic data' : 'SANDBOX: synthetic data', 'sandbox', 'flask'));
  else if (origin === 'live-portal') element.append(chip('Recorded publisher data', '', 'link'));
  else element.append(chip('SANDBOX: data origin unverified', 'sandbox', 'alert'));
  element.append(chip('REPLAY', 'recorded', 'replay'));
  if (includeState) element.append(chip(...status(detail)));
}
function shortTitle(run) {
  return run.title ? run.title.replace(/^Procurement of\s+/i, '') : run.ocid || 'Recorded review';
}
function eligibleRuns() {
  return runs.filter(run => run.title || run.ocid);
}
function renderRuns() {
  const seen = new Set();
  const latest = eligibleRuns().filter(run => {
    const key = run.ocid || run.run_id;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  const list = $('runs'), available = searchResults || latest;
  list.replaceChildren();
  $('ask-badges').hidden = !available.length;
  if (available.length) {
    const origins = new Set(available.map(run => run.origin));
    const origin = origins.size === 1 ? available[0].origin : origins.has('synthetic') || origins.has('mixed') ? 'mixed' : 'not recorded';
    badges('ask-badges', { origin }, false);
  }
  if (!available.length) {
    const empty = node('div', 'empty-state');
    empty.append(node('h3', '', searchResults ? 'No matching recorded review' : 'No recorded award reviews yet'), node('p', '', searchResults ? 'Try the exact contract reference, buyer or title. This viewer can’t start a new investigation.' : 'A requester must run an investigation before it can be reviewed here. Nothing has been generated or assumed.'));
    if (searchResults) {
      const reset = node('button', 'text-button', 'Show all recorded reviews');
      reset.type = 'button'; reset.onclick = () => { searchResults = null; $('award-query').value = ''; $('recent-title').textContent = 'Recorded reviews'; renderRuns(); };
      empty.append(reset);
    }
    list.append(empty);
  }
  available.slice(0, shownRuns).forEach(run => {
    const link = node('a', 'review-row'); link.href = `#review/${encodeURIComponent(run.run_id)}`;
    const copy = node('span'); copy.append(node('span', 'review-row-title', shortTitle(run)), node('span', 'review-row-meta', `${run.buyer_name || 'Buyer not recorded'} · ${stamp(run.started_at)}`));
    const end = node('span', 'review-row-end'); end.append(chip(...status(run)), icon('chevron-right'));
    link.append(copy, end); list.append(link);
  });
  $('show-more').hidden = available.length <= shownRuns;
}
function renderExamples() {
  const examples = $('examples'); examples.replaceChildren();
  published.slice(0, 3).forEach(process => {
    const button = node('button', 'example-chip', shortTitle(process)); button.type = 'button'; button.title = shortTitle(process);
    button.onclick = () => { location.hash = `process/${encodeURIComponent(process.ocid)}`; };
    examples.append(button);
  });
  if (!published.length) examples.append(node('span', 'helper', 'No matching live process. No sandbox example has been substituted.'));
}
function renderPublished() {
  const list = $('live-results'); list.replaceChildren();
  published.forEach(process => {
    const link = node('a', 'review-row'); link.href = `#process/${encodeURIComponent(process.ocid)}`;
    const copy = node('span');
    copy.append(node('span', 'review-row-title', shortTitle(process)), node('span', 'review-row-meta', `${process.buyer_name || 'Buyer not reported'} · FY ${process.fiscal_years.join(', ')}`));
    const end = node('span', 'review-row-end'); end.append(chip(process.award_count ? `${number(process.award_count)} published awards` : 'Tender stage', '', 'file'), icon('chevron-right'));
    link.append(copy, end); list.append(link);
  });
  if (!published.length) list.append(node('p', 'empty-state', 'Not found in downloaded live data. Check the reference, buyer or title; no ID has been guessed and no sandbox data substituted.'));
  renderExamples();
}
async function loadPublisher() {
  try {
    const [status, result] = await Promise.all([api('/api/data/status'), api('/api/data/search?limit=12')]);
    const wrap = $('data-status'); wrap.replaceChildren(chip(status.label, status.label === 'MIXED' || status.label === 'SANDBOX DATA' ? 'sandbox' : '', 'link'));
    wrap.append(node('p', 'helper', `${number(status.counts['live-portal'])} live processes · ${number(status.counts.synthetic)} sandbox records · Publisher data as of ${stamp(status.data_as_of)}`));
    if (status.current_fy_partial) wrap.append(node('p', 'helper partial-warning', 'FY 2026–2027 is in progress and partial. Download completion does not establish full-year coverage.'));
    const details = node('details'); details.append(node('summary', '', 'Source coverage and ingest status'));
    status.per_fy.forEach(fy => {
      const state = status.ingest_states.find(item => item.fiscal_year === fy.fiscal_year);
      details.append(node('p', 'helper', `FY ${fy.fiscal_year} · ${number(fy.releases_observed)} releases · ${number(fy.distinct_ocids)} distinct OCIDs · ${number(fy.with_awards)} with awards · ${state?.status || 'State not recorded'}`));
    });
    details.append(node('p', 'helper', status.coverage_note || 'No publisher coverage measured.'), node('p', 'helper', status.latest_selection_note || ''), node('p', 'helper', `Last ingested ${stamp(status.last_ingested_at)}. License is not reported in the observed PPIP packages.`));
    wrap.append(details); published = initialPublished = result.hits; renderPublished();
  } catch (error) {
    $('data-status').replaceChildren(node('p', 'helper', `Publisher data status unavailable. ${error.message}`));
    $('live-results').replaceChildren(node('p', 'helper', 'Live data could not be loaded. No sandbox fallback was used.'));
    const retry = node('button', 'text-button', 'Try loading publisher data again'); retry.onclick = loadPublisher; $('live-results').append(retry);
  }
}
function renderProcess(process) {
  $('process-badges').replaceChildren(chip('LIVE DATA · downloaded PPIP', '', 'link'));
  if (process.fiscal_years.includes('2026-2027')) $('process-badges').append(chip('Current FY · partial', 'review', 'clock'));
  $('process-title').textContent = process.title || 'Title not reported';
  $('process-reference').textContent = `${process.ocid} · FY ${process.fiscal_years.join(', ')}`;
  const tender = process.record.tender || {}, period = tender.tenderPeriod || {};
  $('process-context').replaceChildren(dataTable([
    ['Buyer', process.buyer_name || 'Not reported'], ['Procurement method', tender.procurementMethod || 'Not reported'],
    ['Tender value', tender.value?.amount == null ? 'Not reported' : amount(tender.value.amount, tender.value.currency)],
    ['Tenderers', tender.numberOfTenderers == null ? 'Not reported' : number(tender.numberOfTenderers)],
    ['Published period start', period.startDate || 'Not reported'], ['Published period end', period.endDate || 'Not reported'],
  ]));
  const awards = process.record.awards || [], wrap = $('process-awards'); wrap.replaceChildren();
  awards.forEach(award => {
    const card = node('article', 'card finding-card');
    card.append(node('h3', '', `Award ${award.id ?? 'identifier not reported'}`), dataTable([
      ['Award value', award.value?.amount == null ? 'Not reported' : `${award.value.currency || 'Currency not reported'} ${number(award.value.amount)}`],
      ['Status', award.status || 'Not reported'], ['Suppliers', (award.suppliers || []).map(supplier => supplier.name || supplier.id).filter(Boolean).join(', ') || 'Not reported'],
    ])); wrap.append(card);
  });
  if (!awards.length) wrap.append(node('p', 'card helper', 'No awards are published in this selected release. This remains a tender-stage contract process; no award has been assumed.'));
  const sourceCard = $('process-source'); sourceCard.replaceChildren(sourceLinks([{source_url: process.source_url, ocid: process.ocid, field_path: 'Selected downloaded release'}], {origin: process.origin}));
  const snapshot = routeLink('View downloaded OCDS record', `/api/data/process?ocid=${encodeURIComponent(process.ocid)}`, 'button secondary full-width', 'code'); snapshot.target = '_blank'; snapshot.rel = 'noopener noreferrer'; sourceCard.append(snapshot);
  sourceCard.append(node('p', 'helper', `Source publication: ${stamp(process.source_publication_date)} · Ingested: ${stamp(process.ingested_at)}. This is an archived download, not a fresh portal check.`));
  if (awards.length && !awards.some(award => award.items?.length)) sourceCard.append(node('p', 'helper', 'Award item classifications are not reported. No price comparables or benchmark has been invented.'));
  sourceCard.append(node('p', 'helper', 'Full fiscal-year coverage is not independently established. Differing releases with the same date have no established chronological ordering.'));
}
async function loadRuns() {
  try { runs = await api('/api/runs'); renderRuns(); renderExamples(); if (screen === 'economics' && !current) renderEconomicsPicker(); }
  catch (error) {
    $('runs').replaceChildren(node('div', 'empty-state', error.message));
    const retry = node('button', 'text-button', 'Try loading reviews again'); retry.type = 'button'; retry.onclick = loadRuns; $('runs').append(retry);
  }
}
function sourceLinks(sources, detail) {
  const wrap = node('div', 'source-links');
  (sources || []).forEach(sourceRef => {
    const raw = sourceRef.source_url || ''; let href = null, label = 'Source link not recorded';
    try {
      const url = new URL(raw.trim());
      if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) { href = url.href; label = url.hostname; }
    } catch (_) {
      if (/(?:^|[/\\])releases\.json(?:$|[?#])/.test(raw) && sourceRef.ocid && ['synthetic', 'mixed'].includes(detail.origin)) {
        href = `/api/runs/${encodeURIComponent(detail.run_id)}/source?ocid=${encodeURIComponent(sourceRef.ocid)}`; label = 'Local OCDS fixture · synthetic';
      }
    }
    const item = node(href ? 'a' : 'span', `source-link ${href ? '' : 'source-unavailable'}`);
    if (href) { item.href = href; item.target = '_blank'; item.rel = 'noopener noreferrer'; }
    const copy = node('span', '', label);
    copy.append(node('span', 'source-field', `${sourceRef.field_path || 'Field not recorded'} · ${sourceRef.ocid || 'Reference not recorded'}`));
    item.append(icon(href ? 'arrow-up-right' : 'link'), copy); wrap.append(item);
  });
  if (!wrap.childNodes.length) {
    const missing = node('span', 'source-link source-unavailable'); missing.append(icon('link'), node('span', '', 'Source link not recorded. Request the evidence before relying on this observation.')); wrap.append(missing);
  }
  return wrap;
}
function contractStrip(detail) {
  const contract = detail.contract || {}, strip = node('div', 'card contract-strip');
  const value = node('div'); value.append(node('span', 'detail-label', 'Award value'), node('span', 'award-number', amount(contract.amount, contract.currency)));
  const buyer = node('div', 'contract-secondary'); buyer.append(node('span', 'detail-label', 'Buyer'), node('strong', '', contract.buyer_name || 'Not recorded'));
  strip.append(value, buyer);
  const note = detail.context_source === 'current fixture' ? 'Contract details are from the current synthetic fixture, not an archived snapshot of this review.' : detail.context_source === 'recorded' ? 'Contract details recorded during the review.' : 'Contract details were not saved with this review.';
  strip.append(node('p', 'context-note', note)); return strip;
}
function setView(nextScreen) {
  screen = nextScreen;
  document.querySelectorAll('[data-screen]').forEach(section => section.hidden = section.dataset.screen !== screen);
  document.querySelectorAll('[data-nav]').forEach(link => {
    if (link.dataset.nav === (['review', 'report', 'approval', 'process'].includes(screen) ? 'ask' : screen)) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  $('main-nav').hidden = screen === 'approval'; $('approval-header').hidden = screen !== 'approval';
  $('run-counter').hidden = screen !== 'review' || !data;
  document.body.classList.toggle('has-counter', screen === 'review' && !!data);
}
function stopStream() {
  if (source) source.close(); source = null;
  clearTimeout(refreshTimer); refreshTimer = null;
}
function connection(label, symbol = 'connection') {
  $('connection-status').replaceChildren(symbol === 'spinner' ? node('span', 'spinner') : icon(symbol), node('span', '', label));
}
function connectStream(detail) {
  if (source || !detail) return;
  const id = detail.run_id; let seen = Math.max(-1, ...detail.events.map(event => event.seq));
  source = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`);
  connection('Connecting to audit updates', 'spinner');
  source.onopen = () => { if (current === id) connection('Audit updates connected'); };
  source.onerror = () => { if (current === id) connection('Updates paused · reconnecting', 'clock'); };
  source.onmessage = event => {
    let item; try { item = JSON.parse(event.data); } catch (_) { return; }
    if (current !== id || !Number.isInteger(item.seq) || item.seq <= seen) return;
    seen = item.seq;
    clearTimeout(refreshTimer);
    connection('Updating recorded work', 'spinner');
    refreshTimer = setTimeout(() => refresh(id), 150);
  };
}
async function refresh(id) {
  if (current !== id) return;
  if (refreshBusy) { refreshQueued = true; return; }
  refreshBusy = true;
  try {
    const updated = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (current === id) {
      if (pendingDecision?.uncertain && (decisionMatches(updated, pendingDecision) || (updated.status === 'awaiting_approval' && updated.request?.seq === pendingDecision.request_seq))) {
        pendingDecision = null; $('decision-confirm').hidden = true;
      }
      data = updated; renderCurrent(); connection('Audit updates connected');
    }
  } catch (error) { if (current === id) { notice(error.message, 'review'); connection('Could not refresh', 'clock'); } }
  finally { refreshBusy = false; if (refreshQueued) { refreshQueued = false; refresh(current); } }
}
function buildTimeline(detail) {
  const events = detail.events;
  const matches = names => events.filter(event => names.includes(event.tool));
  const succeeded = event => event.status === 'ok' || (!event.status && event.outputs && !event.outputs.error);
  const inspected = matches(['inspect_tender']);
  const peers = matches(['benchmark_award']);
  const checks = matches(['evidence_review', 'source_verification', 'investigate_supplier']);
  const gate = detail.request;
  const decision = detail.approval && (!gate || detail.approval.seq > gate.seq) ? detail.approval : null;
  const filed = detail.file_created;
  const steps = [
    { title: 'Finding the contract', done: inspected.some(succeeded), copy: inspected.some(succeeded) ? 'Contract lookup recorded.' : 'A completed lookup was not recorded.' },
    { title: 'Looking for similar contracts', done: peers.some(succeeded), review: peers.some(event => ['timeout', 'error'].includes(event.status)) && !peers.some(succeeded), copy: peers.some(succeeded) ? 'Comparison tool returned a result. Its limits still apply.' : peers.length ? 'The comparison could not be completed. See the tool log.' : 'No comparison step was recorded.' },
    { title: 'Checking what stands out', done: !!detail.review.status && detail.review.status !== 'failed', review: detail.review.status === 'failed', copy: detail.review.status === 'failed' ? 'Reviewer validation did not pass. Manual verification is needed.' : detail.review.status === 'ok' ? `${number(detail.review.retained_count)} observation(s) retained after evidence review.` : checks.length ? 'Evidence checks are recorded; a final reviewer outcome was not saved.' : 'No evidence review outcome recorded.' },
    { title: decision ? 'Committee decision recorded' : 'Waiting for approval', done: !!decision, review: !!gate && !decision, waiting: !!gate && !decision, copy: decision ? `${decision.inputs?.decision === 'reject' ? 'Rejected' : 'Approved'} by ${decision.inputs?.approver_name || 'name not recorded'}.` : gate ? 'A different person must review the evidence and record a decision.' : 'No committee request has been recorded.' },
    { title: filed ? 'File created' : 'File creation', done: filed, copy: filed ? 'The recorded review file is available below.' : detail.status === 'rejected' ? 'This decision does not authorize a new file.' : decision ? 'Decision saved. File creation has not been confirmed; the requester’s runner must continue.' : 'No file is created before a named committee decision.' },
  ];
  const list = $('timeline'); list.replaceChildren();
  steps.forEach(step => {
    const item = node('li', `timeline-step ${step.done ? 'complete' : step.review ? 'review' : ''}`);
    const symbol = node('span', 'step-icon'); symbol.append(step.waiting ? node('span', 'spinner') : icon(step.done ? 'check' : step.review ? 'alert' : 'minus'));
    const copy = node('div'); copy.append(node('div', 'step-title', step.title), node('span', 'step-copy', step.copy));
    item.append(symbol, copy); list.append(item);
  });
}
function renderLogs(detail) {
  $('log-count').textContent = `· ${detail.events.length} recorded actions`;
  $('tool-log').replaceChildren();
  detail.events.forEach(event => {
    const entry = node('details', 'log-entry'), heading = node('summary');
    const state = event.status === 'ok' ? ['Recorded', 'ok', 'check'] : ['timeout', 'error'].includes(event.status) ? [event.status === 'timeout' ? 'Timed out' : 'Needs review', 'review', 'alert'] : event.status === 'retry' ? ['Retry', 'review', 'replay'] : ['Recorded', '', 'file'];
    heading.append(chip(...state), node('span', '', event.tool || 'Audit event'));
    if (event.inputs?.model) heading.append(node('span', 'muted', event.inputs.model));
    entry.append(heading, node('p', 'helper', `${stamp(event.ts_utc)} · ${event.duration_ms == null ? 'Duration not recorded' : `${number(event.duration_ms)} ms`} · ${event.actor || 'Actor not recorded'}`), node('pre', '', JSON.stringify({ inputs: event.inputs, outputs: event.outputs }, null, 2)));
    $('tool-log').append(entry);
  });
}
function renderReview(detail) {
  badges('review-badges', detail);
  $('review-title').textContent = shortTitle(detail);
  $('review-subtitle').textContent = `${detail.buyer_name || 'Buyer not recorded'} · ${detail.ocid || 'Contract reference not recorded'}`;
  $('review-contract').replaceChildren(contractStrip(detail));
  const warning = $('run-warning'); warning.replaceChildren(); warning.hidden = !detail.timeouts.length && detail.review.status !== 'failed';
  if (!warning.hidden) {
    const message = detail.timeouts.length ? detail.timeouts.every(item => item.recovered) ? 'A data-source request timed out. A later attempt succeeded; the interruption remains in the tool log.' : 'Couldn’t reach the data source. Continuing with what we have. This is recorded as a limitation.' : 'The automated reviewer could not validate its result. A manual second look is needed.';
    warning.append(icon('alert'), node('p', '', message));
  }
  buildTimeline(detail); renderLogs(detail);
  const action = $('run-next'); action.replaceChildren();
  if (detail.status === 'awaiting_approval') {
    action.append(routeLink('Open independent approval screen', `#approval/${encodeURIComponent(detail.run_id)}`), node('p', '', 'This screen contains the evidence and limitations a separate approver needs. Invitations are not sent by this viewer.'));
  } else if (detail.report_available && detail.status !== 'rejected') action.append(routeLink('Read the review file', `#report/${encodeURIComponent(detail.run_id)}`));
  else if (detail.approval) action.append(routeLink('See the recorded decision', `#approval/${encodeURIComponent(detail.run_id)}`));
  else action.append(routeLink('View the recorded evidence', `#report/${encodeURIComponent(detail.run_id)}`, 'button secondary'));
  $('counter-tokens').textContent = detail.tokens == null ? 'Tokens not reported' : `${number(detail.tokens)} tokens`;
  $('counter-cost').textContent = detail.cost_per_run == null ? 'List price not configured' : `${cost(detail.cost_per_run)} list price`;
  $('counter-link').href = `#economics/${encodeURIComponent(detail.run_id)}`;
}
function findingMessage(finding) { return finding.statement || finding.message || 'Observation text was not recorded.'; }
function findingDetail(label, value) {
  const wrap = node('div', 'finding-detail'); wrap.append(node('strong', '', label), node('p', '', value || 'Not recorded')); return wrap;
}
function emptyFindings(detail) {
  const empty = node('div', 'empty-state');
  empty.append(node('h3', '', detail.evidence_missing ? 'Evidence details were not saved' : 'No findings retained'));
  if (detail.review.status) {
    empty.append(node('p', '', `Candidates: ${number(detail.review.candidate_count)} · Source-verified: ${number(detail.review.verified_count)} · Retained: ${number(detail.review.retained_count)}`), node('p', '', detail.review.reason || 'The reviewer’s reason was not recorded.'));
  } else empty.append(node('p', '', 'A final evidence review and candidate counts were not recorded. Ask the requester for the work before relying on this review.'));
  empty.append(node('p', '', 'No retained finding does not establish that the award is correct.'));
  return empty;
}
function listLimitations(detail, compact = false) {
  const wrap = node('div');
  const items = detail.limitations;
  if (!items.length) { wrap.append(node('p', 'helper', 'No limitations were recorded. This is not a guarantee that the data is complete.')); return wrap; }
  const list = node('ul'); (compact ? items.slice(0, 2) : items).forEach(item => list.append(node('li', '', item))); wrap.append(list);
  if (compact && items.length > 2) {
    const more = node('details'), heading = node('summary', '', `Read ${items.length - 2} more limitation(s)`), rest = node('ul');
    items.slice(2).forEach(item => rest.append(node('li', '', item))); more.append(heading, rest); wrap.append(more);
  }
  return wrap;
}
function signoff(detail) {
  const wrap = node('div');
  const approval = detail.approval;
  if (!approval || (detail.request && approval.seq < detail.request.seq)) {
    wrap.append(chip('No current sign-off', 'review', 'clock'), node('p', '', 'A current named committee decision has not been recorded.')); return wrap;
  }
  const inputs = approval.inputs || {};
  const automated = /automated|eval-harness/i.test(`${inputs.approver_name} ${inputs.approver_role}`) || /not a human approval/i.test(inputs.reason || '');
  wrap.append(chip(automated ? 'Automated evaluation · not human sign-off' : inputs.decision === 'reject' ? 'Rejected' : 'Named decision recorded', automated || inputs.decision === 'reject' ? 'review' : 'ok', automated ? 'flask' : 'check'));
  wrap.append(node('div', 'signoff-name', inputs.approver_name || 'Name not recorded'), node('p', 'muted', inputs.approver_role || 'Role not recorded'), node('p', '', inputs.reason || 'Reason not recorded'), node('p', 'helper', stamp(approval.ts_utc)));
  if (!automated) wrap.append(node('p', 'helper', 'Name and role are recorded assertions; identity has not been authenticated.'));
  return wrap;
}
function renderApproval(detail) {
  badges('approval-badges', detail, false);
  $('approval-back').href = `#review/${encodeURIComponent(detail.run_id)}`;
  const context = $('approval-context'); context.replaceChildren(chip(...status(detail)), node('h2', 'approval-context-title', shortTitle(detail)), node('p', 'approval-context-meta', `${detail.buyer_name || 'Buyer not recorded'} · ${detail.ocid || 'Reference not recorded'}`));
  const contract = detail.contract || {};
  if (typeof contract.amount === 'number') context.append(node('div', 'award-number', amount(contract.amount, contract.currency)), node('p', 'helper', detail.context_source === 'current fixture' ? 'Value from the current synthetic fixture, not an archived review snapshot.' : 'Value recorded during the review.'));
  if (contract.sources?.length) context.append(sourceLinks(contract.sources, detail));
  $('approval-evidence').replaceChildren();
  detail.findings.slice(0, 2).forEach(finding => {
    const item = node('div', 'mini-finding'); item.append(node('p', '', findingMessage(finding)), sourceLinks(finding.sources, detail)); $('approval-evidence').append(item);
  });
  if (!detail.findings.length) $('approval-evidence').append(emptyFindings(detail));
  if (detail.findings.length > 2) $('approval-evidence').append(routeLink(`Read all ${detail.findings.length} observations`, `#report/${encodeURIComponent(detail.run_id)}`, 'text-button'));
  $('approval-limits').replaceChildren(listLimitations(detail, true));
  const pending = detail.status === 'awaiting_approval';
  if (pendingDecision && pendingDecision.request_seq !== detail.request?.seq) { pendingDecision = null; $('decision-confirm').hidden = true; }
  $('approval-form').hidden = !pending || !!pendingDecision;
  $('approval-state').replaceChildren();
  if (!pending) {
    $('decision-confirm').hidden = true; pendingDecision = null;
    $('approval-state').append(signoff(detail));
    if (detail.file_created) $('approval-state').append(routeLink('File created · read the file', `#report/${encodeURIComponent(detail.run_id)}`, 'button primary full-width'));
    else if (detail.status === 'approved') $('approval-state').append(node('p', 'helper', 'Decision recorded. File creation is not yet confirmed; the requester’s runner must continue.'), routeLink('View recorded progress', `#review/${encodeURIComponent(detail.run_id)}`, 'button secondary full-width'));
    else if (detail.status === 'rejected') $('approval-state').append(node('p', 'helper', 'Decision recorded. This decision does not authorize a new file.'));
  } else {
    if (detail.review.status === 'failed') $('approval-state').append(node('p', 'form-error', 'The automated evidence review failed validation. Verify the underlying sources manually before signing.'));
    if (detail.evidence_missing || !detail.chain_verified) {
      $('approval-state').append(node('p', 'form-error', detail.evidence_missing ? 'The requester has not saved the full reviewed evidence. Request that evidence before approving. You can still record a rejection.' : 'The audit trail could not be verified. No decision can be recorded until it is checked.'));
    }
    $('requester-context').textContent = detail.requester_name ? `Requested by ${detail.requester_name}${detail.requester_role ? ` · ${detail.requester_role}` : ''}. You must be a different person.` : 'Requester identity was not recorded. Confirm that you are reviewing independently.';
    $('approve').disabled = detail.evidence_missing || !detail.chain_verified;
    $('reject').disabled = !detail.chain_verified;
  }
}
function renderReport(detail) {
  badges('report-badges', detail);
  $('report-back').href = `#review/${encodeURIComponent(detail.run_id)}`;
  $('report-eyebrow').textContent = detail.file_created ? 'Filed evidence for committee review' : detail.report_available && detail.status !== 'awaiting_approval' ? 'Earlier recorded file · current filing not confirmed' : 'Recorded evidence · not a filed report';
  $('report-title').textContent = shortTitle(detail);
  $('report-subtitle').textContent = `${detail.buyer_name || 'Buyer not recorded'} · ${detail.ocid || 'Contract reference not recorded'}`;
  $('report-contract').replaceChildren(contractStrip(detail));
  $('findings-count').textContent = detail.findings.length ? `${detail.findings.length} retained observation(s)` : '';
  $('findings').replaceChildren();
  if (!detail.findings.length) { const empty = node('div', 'card'); empty.append(emptyFindings(detail)); $('findings').append(empty); }
  detail.findings.forEach(finding => {
    const card = node('article', 'card finding-card'); card.append(chip('Needs a second look', 'review', 'alert'));
    const message = findingMessage(finding);
    if (typeof finding.claim_value === 'number' && /price|value|benchmark/i.test(`${finding.category || ''} ${finding.code || ''}`)) card.append(node('span', 'finding-number', amount(finding.claim_value, detail.contract.currency)));
    card.append(node('h3', '', finding.category ? finding.category.replace(/[_-]/g, ' ') : 'Recorded observation'), node('p', '', message), findingDetail('Why it matters', finding.rationale), findingDetail('What to check next', finding.follow_up), findingDetail('Uncertainty', finding.uncertainty));
    const sources = node('div', 'finding-sources'); sources.append(sourceLinks(finding.sources, detail)); card.append(sources); $('findings').append(card);
  });
  $('limitations').replaceChildren(listLimitations(detail));
  $('dropped-count').textContent = `· ${detail.dropped_findings.length} recorded`;
  $('dropped').replaceChildren();
  if (!detail.dropped_findings.length) $('dropped').append(node('p', 'helper', 'No dropped findings were recorded.'));
  detail.dropped_findings.forEach(drop => {
    const item = node('div', 'drop-item');
    const finding = drop.finding;
    item.append(node('strong', '', typeof finding === 'object' && finding ? findingMessage(finding) : finding || drop.code || 'Observation'), node('span', '', drop.reason || 'Reason not recorded')); $('dropped').append(item);
  });
  $('signoff').replaceChildren(signoff(detail));
  $('report-action').replaceChildren();
  if (detail.status === 'awaiting_approval') $('report-action').append(routeLink('Continue to independent approval', `#approval/${encodeURIComponent(detail.run_id)}`));
  else if (detail.report_available) $('report-action').append(routeLink(detail.file_created ? 'Open the filed Markdown report' : 'Open the earlier Markdown report', `/api/runs/${encodeURIComponent(detail.run_id)}/report`, 'button secondary', 'arrow-up-right'));
}
function dataTable(rows) {
  const table = node('table', 'data-table'), body = node('tbody');
  rows.forEach(([label, value]) => { const row = node('tr'), heading = node('th', '', label); heading.scope = 'row'; row.append(heading, node('td', '', value)); body.append(row); });
  table.append(body); return table;
}
function renderEconomicsPicker() {
  $('economics-content').hidden = true;
  const wrap = $('economics-select'); wrap.replaceChildren(node('p', 'helper', 'Choose a recorded review to inspect its usage and costs.'));
  const list = node('div', 'review-list');
  eligibleRuns().slice(0, 6).forEach(run => { const link = node('a', 'review-row', shortTitle(run)); link.href = `#economics/${encodeURIComponent(run.run_id)}`; link.append(icon('chevron-right')); list.append(link); });
  if (!list.childNodes.length) list.append(node('p', 'helper', 'No recorded award review is available yet.'));
  wrap.append(list);
}
function renderEconomics(detail) {
  $('economics-select').replaceChildren(); $('economics-content').hidden = false;
  badges('economics-badges', detail, false);
  $('economics-run').textContent = `${shortTitle(detail)} · ${detail.run_id}`;
  const m = detail.metrics, p = m.prices;
  const price = field => field.configured ? `$${field.value} per 1M tokens` : 'Not configured';
  $('pricing-table').replaceChildren(dataTable([
    ['Price basis', 'Currently configured list price, not a provider bill'], ['Model these rates apply to', p.model], ['Plan', p.plan === 'free' ? 'Free plan · list-price estimate, not billed' : p.plan === 'paid' ? 'Paid plan' : 'Not configured'],
    ['Input list price', price(p.LLM_PRICE_IN)], ['Output list price', price(p.LLM_PRICE_OUT)], ['Date entered', p.date_entered || 'Not configured'],
  ]));
  $('cost-table').replaceChildren(dataTable([
    ['Input tokens', number(m.tokens.input_total)], ['Output tokens', number(m.tokens.output_total)], ['Model attempts', number(m.model_calls)], ['Tool calls', number(m.tool_calls.count)],
    ['Recorded time (first to last audit event)', m.wall_time_s == null ? 'Not reported' : `${m.wall_time_s} seconds`], ['Model cost at list price', cost(m.list_price_cost_usd)], ['Cost per outcome (configured fees)', cost(m.cost_per_outcome_usd)],
    ['Exchange rate', m.fx.USD_TO_KES.configured ? `${m.fx.USD_TO_KES.value} KES / USD` : 'Not configured'], ['Outcome cost in KES', m.cost_per_outcome_kes == null ? 'Not configured' : `KES ${number(m.cost_per_outcome_kes)}`],
  ]));
  $('usage-table').replaceChildren();
  m.llm_calls.forEach(call => {
    const row = node('div', 'usage-row'); row.append(node('strong', '', call.model || 'Model not recorded'), node('p', '', `Input ${number(call.input_tokens)} · Output ${number(call.output_tokens)} · Reasoning ${number(call.reasoning_tokens)}`), node('p', '', `${call.status || 'Status not recorded'} · ${call.latency_ms == null ? 'Latency not recorded' : `${number(call.latency_ms)} ms`} · usage ${call.usage}`)); $('usage-table').append(row);
  });
  if (!m.llm_calls.length) $('usage-table').append(node('p', 'helper', 'No model attempts were recorded.'));
  $('economics-notes').replaceChildren(...m.notes.map(note => node('p', '', note)));
}
async function renderIntegrations() {
  try {
    const result = await api('/api/capabilities'); $('integrations').replaceChildren();
    result.integrations.forEach(integration => {
      const row = node('div', 'integration-row'), heading = node('div', 'section-heading');
      const badge = integration.status === 'sandbox' ? ['Sandbox', 'sandbox', 'flask'] : integration.status === 'available' ? ['Available locally', 'ok', 'check'] : integration.status === 'configured' ? ['Configured · not verified', '', 'connection'] : integration.status === 'planned' ? ['Planned', '', 'clock'] : ['Not configured', 'review', 'minus'];
      heading.append(node('h2', '', integration.name), chip(...badge)); row.append(heading, node('p', '', integration.detail)); $('integrations').append(row);
    });
  } catch (error) { $('integrations').replaceChildren(node('p', 'helper', error.message)); }
}
function renderCurrent() {
  if (!data) return;
  if (screen === 'review') renderReview(data);
  else if (screen === 'approval') renderApproval(data);
  else if (screen === 'report') renderReport(data);
  else if (screen === 'economics') renderEconomics(data);
  setView(screen);
  document.title = `${shortTitle(data)} · BidBox`;
}
async function navigate() {
  const version = ++routeVersion;
  let parts; try { parts = (location.hash.slice(1) || 'ask').split('/').map(decodeURIComponent); } catch (_) { parts = ['ask']; }
  const view = ['ask', 'process', 'review', 'approval', 'report', 'economics', 'integrations'].includes(parts[0]) ? parts[0] : 'ask';
  const id = ['process', 'review', 'approval', 'report', 'economics'].includes(view) ? parts[1] || null : null;
  const changed = current !== id;
  if (changed) {
    stopStream(); current = id; data = null; pendingDecision = null;
    $('approval-form').reset(); $('decision-confirm').hidden = true; $('decision-error').hidden = true;
  }
  setView(view); notice('');
  $('main').setAttribute('aria-busy', 'false');
  document.title = 'BidBox · Committee review';
  if (view === 'ask') { renderRuns(); return; }
  if (view === 'integrations') { await renderIntegrations(); return; }
  if (view === 'economics' && !id) { renderEconomicsPicker(); return; }
  if (view === 'process') {
    data = null; $('main').setAttribute('aria-busy', 'true'); notice('Opening downloaded publisher evidence…');
    try {
      if (!id) throw new Error('Contract process reference is missing.');
      const process = await api(`/api/data/process?ocid=${encodeURIComponent(id)}`);
      if (version !== routeVersion) return;
      renderProcess(process); notice(''); document.title = `${process.title || process.ocid} · BidBox`;
    } catch (error) {
      if (version !== routeVersion) return;
      $('process-screen').hidden = true; notice(error.message, 'review'); $('page-notice').append(routeLink('Return to published processes', '#ask', 'text-button', 'arrow-left'));
    } finally { if (version === routeVersion) $('main').setAttribute('aria-busy', 'false'); }
    return;
  }
  if (!id || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(id)) {
    $(`${view}-screen`).hidden = true;
    notice('This review link is incomplete. Return to all reviews and choose a recorded award.', 'review');
    $('page-notice').append(routeLink('Return to recorded reviews', '#ask', 'text-button', 'arrow-left')); return;
  }
  if (data) { renderCurrent(); refresh(id); return; }
  $('approval-form').hidden = true;
  $('main').setAttribute('aria-busy', 'true');
  notice('Opening the recorded review…');
  try {
    const detail = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (version !== routeVersion) return;
    data = detail; notice(''); renderCurrent(); connectStream(detail);
  } catch (error) {
    if (version !== routeVersion) return;
    $(`${view}-screen`).hidden = true;
    notice(error.message, 'review');
    const back = routeLink('Return to recorded reviews', '#ask', 'text-button', 'arrow-left'); $('page-notice').append(back);
  } finally { if (version === routeVersion) $('main').setAttribute('aria-busy', 'false'); }
}
function fillConfirm(snapshot) {
  const approve = snapshot.decision === 'approve';
  $('confirm-title').textContent = approve ? 'Confirm file approval' : 'Confirm rejection';
  $('confirm-copy').textContent = approve ? 'You authorize the requester’s runner to create an investigation file with the recorded evidence and limitations.' : 'You do not authorize a new investigation file from this review. Your reason will be recorded.';
  const details = $('confirm-details'); details.replaceChildren();
  [['Name', snapshot.approver_name], ['Role', snapshot.approver_role], ['Reason', snapshot.reason]].forEach(([label, value]) => details.append(node('dt', '', label), node('dd', '', value)));
  $('confirm-decision').textContent = approve ? 'Confirm approval' : 'Confirm rejection';
  $('confirm-decision').disabled = false; $('edit-decision').disabled = false;
  $('approval-form').hidden = true; $('decision-confirm').hidden = false; $('decision-error').hidden = true;
  $('confirm-title').focus();
}
function decisionMatches(detail, snapshot) {
  const event = detail.approval;
  return event && event.seq > snapshot.request_seq && event.inputs?.decision === snapshot.decision && event.inputs?.approver_name === snapshot.approver_name && event.inputs?.reason === snapshot.reason;
}
async function submitDecision() {
  if (!pendingDecision || !data) return;
  const snapshot = { ...pendingDecision }, id = current;
  const button = $('confirm-decision'); button.disabled = true; $('edit-decision').disabled = true;
  button.replaceChildren(node('span', 'spinner'), node('span', '', 'Recording your decision…'));
  let recorded = false;
  try {
    await api(`/api/runs/${encodeURIComponent(id)}/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapshot) });
    recorded = true;
    const detail = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (current !== id) return;
    data = detail; pendingDecision = null; $('decision-confirm').hidden = true; $('decision-error').hidden = true; renderCurrent();
    $('approval-state').setAttribute('tabindex', '-1'); $('approval-state').focus();
  } catch (error) {
    if (current !== id) return;
    try {
      const detail = await api(`/api/runs/${encodeURIComponent(id)}`);
      if (current !== id) return;
      data = detail;
      if (recorded || decisionMatches(detail, snapshot)) { pendingDecision = null; $('decision-confirm').hidden = true; renderCurrent(); return; }
      if (detail.status === 'awaiting_approval' && detail.request?.seq === snapshot.request_seq) {
        fillConfirm(snapshot); $('decision-error').textContent = `Decision not recorded. ${error.message}`; $('decision-error').hidden = false;
      } else { pendingDecision = null; $('decision-confirm').hidden = true; renderCurrent(); notice('This review changed. Read its current status before making another decision.', 'review'); }
    } catch (_) {
      pendingDecision = { ...snapshot, uncertain: true };
      $('decision-error').textContent = recorded ? 'Your decision was recorded, but file status could not be loaded. Check recorded progress; do not submit the decision again.' : 'The outcome could not be confirmed. Check recorded progress before submitting again.';
      $('decision-error').hidden = false;
      $('approval-state').replaceChildren(routeLink('Check recorded progress', `#review/${encodeURIComponent(id)}`, 'button secondary full-width'));
    }
  }
}
$('ask-form').addEventListener('submit', async event => {
  event.preventDefault(); const query = $('award-query').value.trim();
  if (!query) return;
  const button = $('ask-submit'); button.disabled = true; button.replaceChildren(node('span', 'spinner'), node('span', '', 'Searching publisher data…'));
  $('search-feedback').textContent = 'Searching downloaded live records. No reference will be guessed.';
  try {
    const result = await api(`/api/data/search?q=${encodeURIComponent(query)}&limit=20`);
    if ($('award-query').value.trim() !== query) return;
    published = result.hits; renderPublished();
    if (published.length === 1) { location.hash = `process/${encodeURIComponent(published[0].ocid)}`; return; }
    $('search-feedback').textContent = published.length ? `${published.length} matches returned (maximum 20). Choose the exact published process below.` : 'Not found in live data. No sandbox record or guessed ID has been substituted.';
    $('published-title').setAttribute('tabindex', '-1'); $('published-title').focus();
  } catch (error) { $('search-feedback').textContent = error.message; }
  finally { button.disabled = false; button.replaceChildren(node('span', '', 'Find this process'), icon('arrow-right')); }
});
$('award-query').addEventListener('input', () => {
  $('search-feedback').textContent = '';
  if (!$('award-query').value) { searchResults = null; shownRuns = 4; $('recent-title').textContent = 'Recorded reviews'; renderRuns(); published = initialPublished; renderPublished(); }
});
$('show-more').onclick = () => { shownRuns += 6; renderRuns(); };
$('approval-form').addEventListener('submit', event => {
  event.preventDefault(); if (!data || data.status !== 'awaiting_approval') return;
  const decision = event.submitter?.value;
  if (!['approve', 'reject'].includes(decision)) return;
  const name = $('approver-name').value.trim(), role = $('approver-role').value.trim(), reason = $('reason').value.trim();
  if (!name || !role || !reason || !$('independent-reviewer').checked) {
    $('decision-error').textContent = 'Enter your name, committee role and a short reason, and confirm that you are a different person. Spaces alone do not count.';
    $('decision-error').hidden = false; return;
  }
  if (data.requester_name && data.requester_name.trim().toLowerCase() === name.toLowerCase()) {
    $('decision-error').textContent = 'The approver must be a different person from the requester.'; $('decision-error').hidden = false; return;
  }
  pendingDecision = { approver_name: name, approver_role: role, decision, reason, independent_reviewer: true, request_seq: data.request.seq };
  fillConfirm(pendingDecision);
});
$('edit-decision').onclick = () => { pendingDecision = null; $('decision-confirm').hidden = true; $('approval-form').hidden = false; $('approve').focus(); };
$('confirm-decision').onclick = submitDecision;
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault(); $('main').focus(); $('main').scrollIntoView();
});
window.addEventListener('hashchange', () => { navigate(); window.scrollTo({ top: 0 }); });
window.addEventListener('pagehide', stopStream);
hydrateIcons();
loadPublisher();
loadRuns();
navigate();
