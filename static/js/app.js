document.addEventListener('DOMContentLoaded', () => {
    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const setStatus = (el, msg, kind = '') => {
        el.textContent = msg;
        el.className = 'status-inline' + (kind ? ' ' + kind : '');
    };
    async function api(url, opts = {}) {
        const res = await fetch(url, opts);
        let data = {};
        try { data = await res.json(); } catch (_) { /* no body */ }
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        return data;
    }
    const postJSON = (url, body, method = 'POST') => api(url, {
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });

    // ------------------------------------------------------------------
    // Tab Navigation
    // ------------------------------------------------------------------
    const navLinks = document.querySelectorAll('.nav-links li');
    const tabPanes = document.querySelectorAll('.tab-pane');

    function showTab(tabId) {
        navLinks.forEach(l => l.classList.toggle('active', l.getAttribute('data-tab') === tabId));
        tabPanes.forEach(p => p.classList.toggle('active', p.id === tabId));
        if (tabId === 'screen') updateScreenStats();
        if (tabId === 'ingest') loadIngestStats();
        if (tabId === 'review') loadReviews();
        if (tabId === 'strategy') { loadHarvestSources().then(() => { loadHarvestRuns(); pollHarvestJobs(); }); }
        if (tabId === 'criteria') { if (!criteriaPending) loadCriteriaEditor(); refreshCriteriaResetBox(); setStatus($('reset-status'), ''); }
        if (tabId === 'chat') loadChatScopes();
    }
    navLinks.forEach(link => link.addEventListener('click', () => showTab(link.getAttribute('data-tab'))));
    document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => showTab(b.getAttribute('data-goto'))));

    // ------------------------------------------------------------------
    // Ingestion Logic
    // ------------------------------------------------------------------
    const uploadZone = $('upload-zone');
    const fileInput = $('bib-upload');
    const uploadStatus = $('upload-status');

    uploadZone.addEventListener('click', () => fileInput.click());
    uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
    uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files);
    });
    fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFileUpload(e.target.files); });

    async function handleFileUpload(fileList) {
        uploadStatus.innerHTML = `Uploading and processing files...`;
        const formData = new FormData();
        let fileCount = 0;
        for (let i = 0; i < fileList.length; i++) {
            if (fileList[i].name.endsWith('.bib')) { formData.append("files", fileList[i]); fileCount++; }
        }
        if (fileCount === 0) {
            uploadStatus.innerHTML = `<span class="error">Error: Please upload .bib files.</span>`;
            return;
        }
        try {
            const res = await fetch('/api/ingest', { method: 'POST', body: formData });
            const data = await res.json();
            if (res.ok) {
                uploadStatus.innerHTML = `<span class="success">Loaded ${data.uploaded} records: ${data.new_unique} new unique, ${data.new_duplicates} duplicates of records already in the corpus. Corpus: ${data.total_unique_db} unique.</span>`;
                loadIngestStats();
            } else {
                uploadStatus.innerHTML = `<span class="error">Error: ${esc(data.error)}</span>`;
            }
        } catch (err) {
            uploadStatus.innerHTML = `<span class="error">Network error.</span>`;
        }
    }

    // ------------------------------------------------------------------
    // Ingestion dashboard + "ingest all harvested files"
    // ------------------------------------------------------------------
    async function loadIngestStats() {
        try {
            const d = await api(`/api/ingest/stats?limit=${DUP_PAGE}`);
            $('ingest-stat-total').textContent = d.total_records;
            $('ingest-stat-unique').textContent = d.unique;
            $('ingest-stat-dups').textContent = d.duplicates;
            $('ingest-stat-sources').textContent = d.sources.length;
            $('ingest-dups-tag').textContent = d.duplicates;
            const srcBody = $('ingest-sources-table').querySelector('tbody');
            srcBody.innerHTML = d.sources.map(s =>
                `<tr><td>${esc(s.source_file)}</td><td class="num">${s.records}</td><td class="num">${s.duplicates}</td><td class="num">${s.records - s.duplicates}</td></tr>`
            ).join('') || '<tr><td colspan="4" class="info-text">No records yet.</td></tr>';
            renderDuplicatePage(d.duplicate_list, 0, d.duplicates);
        } catch (e) { console.error('Failed to load ingestion stats', e); }
        try {
            const r = await api('/api/harvest/runs');
            const body = $('ingest-runs-table').querySelector('tbody');
            body.innerHTML = r.runs.map(run => {
                const res = run.result || {};
                const status = run.status === 'done' ? '<span class="tag ok">done</span>'
                    : `<span class="tag warn" title="${esc(run.error || '')}">failed</span>`;
                return `<tr><td>${esc(databases[run.source] || run.source)}</td><td>${esc((run.started || '').replace('T', ' '))}</td>
                    <td>${esc(run.years_label || '')}</td><td class="num">${res.count ?? ''}</td>
                    <td class="num">${res.new_unique ?? ''}</td><td class="num">${res.new_duplicates ?? ''}</td><td>${status}</td></tr>`;
            }).join('') || '<tr><td colspan="7" class="info-text">No harvest runs yet. Run a query on the Search Strategy tab.</td></tr>';
        } catch (_) { /* ignore */ }
    }

    // Flush: start over with an empty corpus (records, harvest runs, screening results).
    $('btn-flush-corpus').addEventListener('click', async () => {
        const st = $('flush-status');
        const total = $('ingest-stat-total').textContent;
        if (!confirm(`Delete ALL ingested data?\n\nThis removes ${total} records (including duplicates), every harvest run and every screening result, including human review decisions.\n\nYour criteria and search strategy are kept. This cannot be undone.`)) return;
        const btn = $('btn-flush-corpus');
        btn.disabled = true;
        setStatus(st, 'Deleting…');
        try {
            const r = await api('/api/ingest/all', { method: 'DELETE' });
            setStatus(st, `Removed ${r.records} records, ${r.harvests} harvest runs and ${r.screening_results} screening results.`, 'success');
            loadIngestStats();
            updateScreenStats();
            loadHarvestRuns();
        } catch (e) {
            setStatus(st, 'Error: ' + e.message, 'error');
        } finally { btn.disabled = false; }
    });

    // Duplicates table: 30 rows per page with numbered pages (search-engine
    // style: Prev, a window of up to 10 page numbers around the current one, Next).
    const DUP_PAGE = 30;
    const dupRow = (r) => `<tr>
                <td>${esc(r.title)}<span class="meta">${esc(r.year || '')}${r.doi ? ' · ' + esc(r.doi) : ''}</span></td>
                <td>${esc(r.source_file)}</td>
                <td>${esc(r.reason || '')}</td>
                <td>${esc(r.canonical_title)}<span class="meta">${esc(r.canonical_source)}</span></td></tr>`;
    function renderDuplicatePage(items, offset, total) {
        const body = $('ingest-dups-table').querySelector('tbody');
        body.innerHTML = total ? items.map(dupRow).join('')
            : '<tr><td colspan="4" class="info-text">No duplicates detected.</td></tr>';
        const first = total ? offset + 1 : 0;
        const last = Math.min(offset + items.length, total);
        $('ingest-dups-more').textContent = total ? `Showing ${first}\u2013${last} of ${total} duplicates.` : '';
        renderPager($('dups-pager'), Math.floor(offset / DUP_PAGE) + 1, Math.ceil(total / DUP_PAGE), loadDuplicatePage);
    }
    async function loadDuplicatePage(page) {
        try {
            const d = await api(`/api/ingest/duplicates?offset=${(page - 1) * DUP_PAGE}&limit=${DUP_PAGE}`);
            renderDuplicatePage(d.items, d.offset, d.total);
            $('ingest-dups-details').scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (e) { $('ingest-dups-more').textContent = 'Could not load page: ' + e.message; }
    }
    function renderPager(nav, current, pages, go) {
        nav.innerHTML = '';
        if (pages <= 1) return;
        const add = (label, page, opts = {}) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'pager-btn' + (opts.current ? ' current' : '');
            b.textContent = label;
            b.disabled = !!opts.disabled || !!opts.current;
            if (opts.current) b.setAttribute('aria-current', 'page');
            if (opts.label) b.setAttribute('aria-label', opts.label);
            b.addEventListener('click', () => go(page));
            nav.appendChild(b);
        };
        const gap = () => { const s = document.createElement('span'); s.className = 'pager-gap'; s.textContent = '…'; nav.appendChild(s); };
        add('‹ Prev', current - 1, { disabled: current === 1, label: 'Previous page' });
        const window_ = 10;
        let start = Math.max(1, current - Math.floor(window_ / 2));
        let end = Math.min(pages, start + window_ - 1);
        start = Math.max(1, end - window_ + 1);
        if (start > 1) { add('1', 1); if (start > 2) gap(); }
        for (let p = start; p <= end; p++) add(String(p), p, { current: p === current });
        if (end < pages) { if (end < pages - 1) gap(); add(String(pages), pages); }
        add('Next ›', current + 1, { disabled: current === pages, label: 'Next page' });
    }

    // ------------------------------------------------------------------
    // Search Strategy
    // ------------------------------------------------------------------
    let databases = {};        // key -> label
    let harvestSources = {};   // key -> {available, manual, note}
    let savedStrategy = null;

    const conceptsContainer = $('concepts-container');
    const queriesContainer = $('queries-container');

    function renderConcept(concept = { name: '', terms: [] }) {
        const row = document.createElement('div');
        row.className = 'concept-row';
        row.innerHTML = `
            <div class="row-between">
                <input type="text" class="concept-name input-md" placeholder="Concept name" value="${esc(concept.name)}">
                <button class="btn-outline btn-sm concept-remove" title="Remove concept">✕</button>
            </div>
            <textarea class="concept-terms mt-10" rows="2" placeholder="Terms separated by ; (synonyms, variants, truncation*)"></textarea>`;
        row.querySelector('.concept-terms').value = (concept.terms || []).join('; ');
        row.querySelector('.concept-remove').addEventListener('click', () => { row.remove(); scheduleStrategySave(); });
        conceptsContainer.appendChild(row);
    }

    function renderQueryRow(key, value) {
        const label = databases[key] || key;
        const src = harvestSources[key];
        const row = document.createElement('div');
        row.className = 'query-row';
        row.dataset.key = key;
        let tag, note = src ? src.note : '';
        if (!src) tag = '<span class="tag muted">no harvester</span>';
        else if (src.manual) tag = '<span class="tag warn">manual export</span>';
        else if (src.available) tag = '<span class="tag ok">API ready</span>';
        else tag = '<span class="tag warn">needs API key</span>';
        const canRun = src && !src.manual && src.available;
        row.innerHTML = `
            <div class="query-head">
                <div><span class="query-title">${esc(label)}</span> ${tag}</div>
                <div class="btn-row">
                    <button class="btn-outline btn-sm q-copy">Copy</button>
                    ${canRun ? '<button class="btn-primary btn-sm q-run">▶ Run harvest</button>' : ''}
                </div>
            </div>
            <textarea class="mono q-text" rows="3"></textarea>
            ${note ? `<div class="query-note mt-10">${esc(note)}</div>` : ''}`;
        row.querySelector('.q-text').value = value || '';
        row.querySelector('.q-copy').addEventListener('click', async (e) => {
            try { await navigator.clipboard.writeText(row.querySelector('.q-text').value); e.target.textContent = 'Copied!'; }
            catch (_) { e.target.textContent = 'Copy failed'; }
            setTimeout(() => e.target.textContent = 'Copy', 1500);
        });
        const runBtn = row.querySelector('.q-run');
        if (runBtn) runBtn.addEventListener('click', () => startHarvest(key, row.querySelector('.q-text').value, runBtn));
        queriesContainer.appendChild(row);
    }

    function renderStrategy(strategy) {
        const s = strategy || { research_question: '', scope_notes: '', concepts: [], queries: {}, rationale: '', limitations: '' };
        $('strategy-topic').value = s.research_question || '';
        $('strategy-scope').value = s.scope_notes || '';
        $('strategy-rationale').value = s.rationale || '';
        $('strategy-limitations').value = s.limitations || '';
        conceptsContainer.innerHTML = '';
        (s.concepts || []).forEach(renderConcept);
        queriesContainer.innerHTML = '';
        const keys = Object.keys(databases);
        for (const k of Object.keys(s.queries || {})) if (!keys.includes(k)) keys.push(k);
        keys.forEach(k => renderQueryRow(k, (s.queries || {})[k] || ''));
    }

    function collectStrategy() {
        const concepts = [...conceptsContainer.querySelectorAll('.concept-row')].map(row => ({
            name: row.querySelector('.concept-name').value.trim(),
            terms: row.querySelector('.concept-terms').value.split(';').map(t => t.trim()).filter(Boolean)
        })).filter(c => c.name || c.terms.length);
        const queries = {};
        queriesContainer.querySelectorAll('.query-row').forEach(row => {
            queries[row.dataset.key] = row.querySelector('.q-text').value.trim();
        });
        return {
            research_question: $('strategy-topic').value.trim(),
            scope_notes: $('strategy-scope').value.trim(),
            concepts, queries,
            rationale: $('strategy-rationale').value.trim(),
            limitations: $('strategy-limitations').value.trim()
        };
    }

    async function loadHarvestSources() {
        if (Object.keys(harvestSources).length) return;
        try {
            const data = await api('/api/harvest/sources');
            data.sources.forEach(s => harvestSources[s.key] = s);
        } catch (e) { console.error(e); }
    }

    async function loadStrategy() {
        try {
            const data = await api('/api/search-strategy');
            databases = data.databases || {};
            savedStrategy = data.strategy;
            renderStrategy(savedStrategy);
            updateStrategyAIButton();
            if (savedStrategy && savedStrategy.research_question && !$('criteria-topic').value) {
                $('criteria-topic').placeholder = savedStrategy.research_question;
            }
        } catch (e) { console.error('Failed to load strategy', e); }
    }

    $('btn-add-concept').addEventListener('click', () => renderConcept());

    // One AI button: it refines the current strategy (concepts, queries,
    // scope notes + the feedback text) when one exists, and generates from
    // the research question alone when the tab is empty. "Start over" forces
    // a fresh generation.
    function hasStrategy() {
        const s = collectStrategy();
        return s.concepts.some(c => c.terms.length) || Object.values(s.queries).some(q => q);
    }
    function updateStrategyAIButton() {
        const refine = hasStrategy();
        const btn = $('btn-strategy-generate');
        btn.textContent = refine ? '↻ Refine with AI' : '✨ Generate with AI';
        btn.title = refine
            ? 'Send the current concepts, queries, scope notes and your feedback to the LLM and improve them'
            : 'Ask the LLM to draft concepts and database queries from the research question';
        $('btn-strategy-restart').classList.toggle('hidden', !refine);
    }

    async function runStrategyAI(refine) {
        const status = $('strategy-ai-status');
        const topic = $('strategy-topic').value.trim();
        if (!topic && !(refine && savedStrategy)) { setStatus(status, 'Enter a research question first.', 'error'); return; }
        const btns = [$('btn-strategy-generate'), $('btn-strategy-restart')];
        btns.forEach(b => b.disabled = true);
        setStatus(status, refine ? 'Refining with the LLM… (can take a minute)' : 'Asking the LLM… (can take a minute)');
        try {
            const body = { topic, feedback: $('strategy-feedback').value.trim() };
            if (refine) body.current = collectStrategy();
            const data = await postJSON('/api/search-strategy/generate', body);
            renderStrategy(data.strategy);
            await saveStrategyNow();
            setStatus(status, refine ? 'Refined and saved. Edit freely; changes save automatically.'
                                     : 'Draft saved. Review and edit; changes save automatically.', 'success');
        } catch (e) {
            setStatus(status, 'Error: ' + e.message, 'error');
        } finally { btns.forEach(b => b.disabled = false); updateStrategyAIButton(); }
    }
    $('btn-strategy-generate').addEventListener('click', () => runStrategyAI(hasStrategy()));
    $('btn-strategy-restart').addEventListener('click', () => {
        if (!confirm('Discard the current concepts, scope notes and queries and generate a new strategy from the research question only?')) return;
        runStrategyAI(false);
    });
    $('strategy').addEventListener('input', updateStrategyAIButton);

    // Deterministic rebuild: concepts -> queries, no LLM. Replaces the query
    // boxes (unsaved until Save).
    $('btn-strategy-build').addEventListener('click', async () => {
        const status = $('strategy-build-status');
        const btn = $('btn-strategy-build');
        btn.disabled = true;
        setStatus(status, 'Building…');
        try {
            const current = collectStrategy();
            const data = await postJSON('/api/search-strategy/build',
                { concepts: current.concepts, scope_notes: current.scope_notes });
            const rows = {};
            queriesContainer.querySelectorAll('.query-row').forEach(r => { rows[r.dataset.key] = r; });
            Object.entries(data.queries).forEach(([key, q]) => {
                if (rows[key]) rows[key].querySelector('.q-text').value = q;
                else renderQueryRow(key, q);
            });
            let msg = `Rebuilt ${Object.keys(data.queries).length} queries from ${data.concept_count} concept(s), no LLM call.`;
            if (data.year_range_label) {
                const sup = data.year_filter_support || {};
                const inQuery = Object.keys(sup).filter(k => sup[k] === 'query').map(k => databases[k] || k);
                const viaApi = Object.keys(sup).filter(k => sup[k] === 'api').map(k => databases[k] || k);
                const manual = Object.keys(sup).filter(k => sup[k] === 'manual').map(k => databases[k] || k);
                msg += ` Years ${data.year_range_label} from the scope notes: written into the query for ${inQuery.join(', ')};`
                    + ` applied as an API filter when harvesting ${viaApi.join(', ')};`
                    + ` ${manual.join(', ')}: set the date filter on the website.`;
            } else {
                msg += ' No year range found in the scope notes (write e.g. "2010 onwards" or "2015-2024" to add a date filter).';
            }
            setStatus(status, msg, 'success');
            await saveStrategyNow();
        } catch (e) {
            setStatus(status, 'Error: ' + e.message, 'error');
        } finally { btn.disabled = false; }
    });

    // Auto-save: the strategy is persisted a moment after the last edit (and
    // immediately after Generate / Refine / Rebuild). No Save button.
    let strategySaveTimer = null;
    let strategySaving = null;      // in-flight promise
    let strategySaveAgain = false;  // an edit arrived while saving

    async function saveStrategyNow() {
        if (strategySaving) { strategySaveAgain = true; return strategySaving; }
        clearTimeout(strategySaveTimer);
        const status = $('strategy-save-status');
        setStatus(status, 'Saving…');
        strategySaving = (async () => {
            try {
                const data = await postJSON('/api/search-strategy', collectStrategy(), 'PUT');
                savedStrategy = data.strategy;
                const t = new Date();
                setStatus(status, `Saved ${t.getHours()}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`, 'success');
            } catch (e) {
                setStatus(status, 'Not saved: ' + e.message, 'error');
            } finally {
                strategySaving = null;
                if (strategySaveAgain) { strategySaveAgain = false; saveStrategyNow(); }
            }
        })();
        return strategySaving;
    }
    function scheduleStrategySave() {
        clearTimeout(strategySaveTimer);
        setStatus($('strategy-save-status'), 'Editing…');
        strategySaveTimer = setTimeout(saveStrategyNow, 900);
    }
    // Any edit inside the tab (concept names/terms, scope notes, queries,
    // research question, rationale) schedules a save. Feedback and the
    // harvest size are not part of the strategy.
    $('strategy').addEventListener('input', (e) => {
        const el = e.target;
        if (!el || el.id === 'strategy-feedback' || el.id === 'harvest-max') return;
        if (!el.matches('input, textarea')) return;
        scheduleStrategySave();
    });
    window.addEventListener('beforeunload', () => { if (strategySaveTimer) { clearTimeout(strategySaveTimer); saveStrategyNow(); } });

    // --- Harvesting -----------------------------------------------------
    let harvestPoll = null;

    async function startHarvest(source, query, btn) {
        if (!query.trim()) { alert('Query is empty.'); return; }
        btn.disabled = true;
        try {
            await postJSON('/api/harvest', { source, query, max_results: parseInt($('harvest-max').value || '500', 10),
                scope_notes: $('strategy-scope').value });
            pollHarvestJobs();
            $('harvest-jobs').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (e) { alert('Harvest failed to start: ' + e.message); }
        finally { btn.disabled = false; }
    }

    async function pollHarvestJobs() {
        try {
            const data = await api('/api/harvest/jobs');
            const box = $('harvest-jobs');
            box.innerHTML = '';
            let anyRunning = false;
            data.jobs.forEach(j => {
                if (j.status === 'running') anyRunning = true;
                const div = document.createElement('div');
                div.className = 'harvest-job';
                const label = databases[j.source] || j.source;
                let right = '';
                if (j.status === 'running') {
                    const pct = j.total ? Math.min(100, Math.round(100 * j.fetched / Math.min(j.total, j.max_results))) : 0;
                    right = `<div class="btn-row"><span class="meta">${j.fetched}${j.total != null ? ' / ' + Math.min(j.total, j.max_results) : ''} fetched</span>
                             <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div></div>`;
                } else {
                    right = `<span class="tag warn" title="${esc(j.error)}">failed: ${esc((j.error || '').slice(0, 120))}</span>`;
                }
                const yrs = j.years_label ? ` <span class="tag">years ${esc(j.years_label)}</span>` : '';
                div.innerHTML = `<div><strong>${esc(label)}</strong>${yrs} <span class="meta">${esc(j.started.replace('T', ' '))}</span><br>
                                 <span class="meta mono">${esc(j.query.slice(0, 160))}${j.query.length > 160 ? '…' : ''}</span></div>${right}`;
                box.appendChild(div);
            });
            if (anyRunning) {
                if (!harvestPoll) harvestPoll = setInterval(pollHarvestJobs, 2000);
            } else if (harvestPoll) {
                clearInterval(harvestPoll); harvestPoll = null;
                loadHarvestRuns(); loadIngestStats();
            }
        } catch (e) { console.error(e); }
    }

    async function loadHarvestRuns() {
        const box = $('harvest-runs');
        try {
            const data = await api('/api/harvest/runs');
            if (!data.runs.length) { box.innerHTML = '<p class="info-text">No harvest runs yet. Press <strong>Run harvest</strong> on a query above.</p>'; return; }
            box.innerHTML = '';
            data.runs.forEach(run => {
                const res = run.result || {};
                const div = document.createElement('div');
                div.className = 'harvest-file';
                const label = databases[run.source] || run.source;
                const yrs = run.years_label ? ` <span class="tag">years ${esc(run.years_label)}</span>` : '';
                const state = run.status === 'done'
                    ? `<span class="tag ok">done · ${res.count} fetched · ${res.new_unique} new unique · ${res.new_duplicates} duplicates</span>`
                    : `<span class="tag warn" title="${esc(run.error || '')}">failed: ${esc((run.error || '').slice(0, 100))}</span>`;
                div.innerHTML = `<div><strong>${esc(label)}</strong>${yrs} <span class="meta">${esc((run.started || '').replace('T', ' '))}</span><br>
                        <span class="meta mono">${esc((run.query || '').slice(0, 160))}${(run.query || '').length > 160 ? '…' : ''}</span><br>${state}</div>
                    <div class="btn-row">
                        ${run.status === 'done' ? `<a class="btn-outline btn-sm" href="/api/harvest/runs/${encodeURIComponent(run.id)}/download" style="text-decoration:none">⬇ Download .bib</a>` : ''}
                        <button class="btn-outline btn-sm r-delete" title="Remove this run and the unscreened records it added">✕</button>
                        <span class="status-inline r-status"></span>
                    </div>`;
                div.querySelector('.r-delete').addEventListener('click', async () => {
                    if (!confirm(`Remove this ${label} run and the records it added (records already screened are kept)?`)) return;
                    try {
                        const r = await api(`/api/harvest/runs/${encodeURIComponent(run.id)}`, { method: 'DELETE' });
                        setStatus(div.querySelector('.r-status'), `Removed ${r.records_removed} records${r.records_kept ? `, kept ${r.records_kept} screened` : ''}.`, 'success');
                        setTimeout(() => { loadHarvestRuns(); loadIngestStats(); }, 800);
                    } catch (err) { alert(err.message); }
                });
                box.appendChild(div);
            });
        } catch (e) { box.innerHTML = '<p class="error">Failed to list harvest runs.</p>'; }
    }

    // ------------------------------------------------------------------
    // Criteria editor
    // ------------------------------------------------------------------
    const criteriaEditor = $('criteria-editor');
    const criterionTemplate = $('criterion-template');

    // Auto-save: criteria.json is written about a second after the last edit
    // (immediately after an LLM draft). A save the server refuses - invalid
    // key, missing name/definition, or screening running (409) - keeps the
    // editor as it is, shows why, and is retried on the next edit or once
    // screening stops.
    let criteriaSaveTimer = null;
    let criteriaSaving = null;
    let criteriaSaveAgain = false;
    let criteriaPending = false;     // edits not yet accepted by the server

    async function saveCriteriaNow() {
        if (criteriaSaving) { criteriaSaveAgain = true; return criteriaSaving; }
        clearTimeout(criteriaSaveTimer);
        const status = $('criteria-save-status');
        setStatus(status, 'Saving…');
        criteriaSaving = (async () => {
            try {
                await postJSON('/api/criteria', collectCriteria(), 'PUT');
                criteriaPending = false;
                const t = new Date();
                setStatus(status, `Saved ${t.getHours()}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')} - the next screened record uses these criteria.`, 'success');
                loadCriteria();
            } catch (e) {
                criteriaPending = true;
                const waiting = /stop screening/i.test(e.message);
                setStatus(status, (waiting ? 'Not saved yet: screening is running, will save when it stops. ' : 'Not saved: ') + e.message, 'error');
            } finally {
                criteriaSaving = null;
                if (criteriaSaveAgain) { criteriaSaveAgain = false; saveCriteriaNow(); }
            }
        })();
        return criteriaSaving;
    }
    function markCriteriaDirty() {
        criteriaPending = true;
        clearTimeout(criteriaSaveTimer);
        setStatus($('criteria-save-status'), 'Editing…');
        criteriaSaveTimer = setTimeout(saveCriteriaNow, 1000);
    }
    window.addEventListener('beforeunload', () => { if (criteriaSaveTimer) { clearTimeout(criteriaSaveTimer); saveCriteriaNow(); } });

    function renderCriterion(key = '', c = {}) {
        const clone = criterionTemplate.content.cloneNode(true);
        const card = clone.querySelector('.criterion-card');
        card.querySelector('.crit-key').value = key;
        card.querySelector('.crit-name').value = c.name || '';
        card.querySelector('.crit-definition').value = c.definition || '';
        card.querySelector('.crit-signals').value = c.signals || '';
        card.querySelector('.crit-negative').value = c.negative_indicators || '';
        card.querySelector('.crit-remove').addEventListener('click', () => { card.remove(); markCriteriaDirty(); });
        card.querySelectorAll('input, textarea').forEach(el => el.addEventListener('input', () => markCriteriaDirty()));
        criteriaEditor.appendChild(clone);
    }

    function renderCriteriaEditor(criteria) {
        criteriaEditor.innerHTML = '';
        Object.entries(criteria || {}).forEach(([k, c]) => renderCriterion(k, c));
    }

    function collectCriteria() {
        const out = {};
        criteriaEditor.querySelectorAll('.criterion-card').forEach(card => {
            const key = card.querySelector('.crit-key').value.trim();
            if (!key) return;
            out[key] = {
                name: card.querySelector('.crit-name').value.trim(),
                definition: card.querySelector('.crit-definition').value.trim(),
                signals: card.querySelector('.crit-signals').value.trim(),
                negative_indicators: card.querySelector('.crit-negative').value.trim()
            };
        });
        return out;
    }

    async function loadCriteriaEditor() {
        try {
            const data = await api('/api/criteria');
            renderCriteriaEditor(data);
            criteriaPending = false;
        } catch (e) { console.error(e); }
    }

    $('btn-add-criterion').addEventListener('click', () => {
        const n = criteriaEditor.querySelectorAll('.criterion-card').length + 1;
        renderCriterion(`IC${n}`, {});
        // Saved once the new criterion has a name and a definition.
        setStatus($('criteria-save-status'), 'New criterion: fill in a name and a definition to save it.');
    });

    async function runCriteriaAI(refine) {
        const status = $('criteria-ai-status');
        const btns = [$('btn-criteria-draft'), $('btn-criteria-refine')];
        btns.forEach(b => b.disabled = true);
        setStatus(status, 'Asking the LLM… (can take a minute)');
        try {
            const body = {
                topic: $('criteria-topic').value.trim(),
                feedback: $('criteria-feedback').value.trim(),
                count: $('criteria-count').value ? parseInt($('criteria-count').value, 10) : null
            };
            if (refine) body.current = collectCriteria();
            const data = await postJSON('/api/criteria/generate', body);
            renderCriteriaEditor(data.criteria);
            await saveCriteriaNow();
            setStatus(status, criteriaPending ? 'Draft ready but not saved yet (see below).' : 'Draft saved. Edit freely; changes save automatically.', criteriaPending ? 'error' : 'success');
        } catch (e) { setStatus(status, 'Error: ' + e.message, 'error'); }
        finally { btns.forEach(b => b.disabled = false); }
    }
    $('btn-criteria-draft').addEventListener('click', () => runCriteriaAI(false));
    $('btn-criteria-refine').addEventListener('click', () => runCriteriaAI(true));


    // The "changing criteria after screening?" box only makes sense while
    // screening results exist; it disappears once they are reset.
    const RESET_BOX_DISMISSED = 'prismaowl-reset-box-dismissed';
    async function refreshCriteriaResetBox() {
        try {
            const st = await api('/api/screen/status');
            const n = st.screened || 0;
            let dismissed = false;
            try { dismissed = sessionStorage.getItem(RESET_BOX_DISMISSED) === '1'; } catch (_) { /* private mode */ }
            $('criteria-reset-box').classList.toggle('hidden', n === 0 || dismissed);
            $('criteria-reset-count').textContent = n === 1 ? '1 screening result' : `${n} screening results`;
        } catch (_) { /* ignore */ }
    }
    $('btn-reset-box-close').addEventListener('click', () => {
        $('criteria-reset-box').classList.add('hidden');
        try { sessionStorage.setItem(RESET_BOX_DISMISSED, '1'); } catch (_) { /* ignore */ }
    });

    $('btn-reset-results').addEventListener('click', async () => {
        if (!confirm('Delete ALL screening results (including human review decisions)? Records are kept.')) return;
        const st = $('reset-status');
        const btn = $('btn-reset-results');
        btn.disabled = true;
        try {
            const r = await api('/api/screen/results', { method: 'DELETE' });
            $('criteria-reset-box').classList.add('hidden');
            setStatus(st, `Deleted ${r.deleted} screening results. The corpus will be re-screened on the next run.`, 'success');
            updateScreenStats();
        } catch (e) { setStatus(st, 'Error: ' + e.message, 'error'); }
        finally { btn.disabled = false; }
    });

    // ------------------------------------------------------------------
    // Screening Logic
    // ------------------------------------------------------------------
    const btnStartScreen = $('btn-start-screen');
    const screenProgress = $('screen-progress');
    const progressText = $('progress-text');
    const statTotal = $('stat-total');
    const statScreened = $('stat-screened');
    let screenInterval;

    async function updateScreenStats() {
        const res = await fetch('/api/screen/status');
        const data = await res.json();
        statTotal.textContent = data.total;
        statScreened.textContent = data.screened;

        renderStrictness(data);
        const modelEl = $('screen-model');
        const modelWarn = $('screen-model-warning');
        if (modelEl) modelEl.textContent = data.model || '(not configured)';
        if (modelWarn) {
            modelWarn.textContent = data.model_ok ? '' : data.model_error;
            modelWarn.classList.toggle('hidden', !!data.model_ok);
        }

        if (data.is_running) {
            btnStartScreen.disabled = true;
            screenProgress.classList.remove('hidden');
            const pct = data.total > 0 ? Math.round((data.screened / data.total) * 100) : 0;
            $('progress-fill').style.width = pct + '%';
            progressText.textContent = `Screening in progress... ${data.screened}/${data.total} (${pct}%)`;
            if (!screenInterval) screenInterval = setInterval(updateScreenStats, 3000);
        } else {
            btnStartScreen.disabled = !data.model_ok;
            screenProgress.classList.add('hidden');
            if (screenInterval) { clearInterval(screenInterval); screenInterval = null; }
            if (criteriaPending && !criteriaSaving && !criteriaSaveTimer) saveCriteriaNow();
        }
    }

    // Decision strictness: three prompt rule sets, saved to .env as
    // SCREENING_STRICTNESS through the settings endpoint; recorded on every
    // result and guarded like the model pin (no mixing within one review).
    let strictnessRendered = false;
    function renderStrictness(status) {
        const box = $('screen-strictness');
        const levels = status.strictness_levels || {};
        if (!strictnessRendered) {
            box.innerHTML = Object.entries(levels).map(([key, lv]) => `
                <label class="segment"><input type="radio" name="screen-strictness" value="${key}">
                    <span><strong>${esc(lv.label)}</strong></span></label>`).join('');
            box.querySelectorAll('input').forEach(r => r.addEventListener('change', () => setStrictness(r.value)));
            strictnessRendered = true;
        }
        box.querySelectorAll('input').forEach(r => { r.checked = r.value === status.strictness; r.disabled = !!status.is_running; });
        $('screen-strictness-help').textContent = (levels[status.strictness] || {}).summary || '';
    }
    async function setStrictness(level) {
        const st = $('screen-strictness-status');
        setStatus(st, 'Saving…');
        try {
            await postJSON('/api/settings', { SCREENING_STRICTNESS: level }, 'PUT');
            setStatus(st, 'Saved. Applies to every record screened from now on.', 'success');
            updateScreenStats();
        } catch (e) {
            setStatus(st, 'Not saved: ' + e.message, 'error');
            updateScreenStats();
        }
    }

    btnStartScreen.addEventListener('click', async () => {
        btnStartScreen.disabled = true;
        screenProgress.classList.remove('hidden');
        progressText.textContent = "Starting AI Engine...";
        try {
            await api('/api/screen/start', { method: 'POST' });
            if (!screenInterval) screenInterval = setInterval(updateScreenStats, 3000);
        } catch (e) {
            progressText.textContent = `Failed to start screening: ${e.message}`;
            updateScreenStats();
        }
    });

    const btnStopScreen = $('btn-stop-screen');
    if (btnStopScreen) {
        btnStopScreen.addEventListener('click', async () => {
            await fetch('/api/screen/stop', { method: 'POST' });
            clearInterval(screenInterval);
            screenInterval = null;
            screenProgress.classList.add('hidden');
            btnStartScreen.disabled = false;
        });
    }

    // ------------------------------------------------------------------
    // Review Logic
    // ------------------------------------------------------------------
    const reviewArea = $('review-area');
    const reviewTemplate = $('review-template');

    let activeCriteria = {};

    async function loadCriteria() {
        try {
            const res = await fetch('/api/criteria');
            activeCriteria = await res.json();
            const listEl = $('active-criteria-list');
            if (listEl) {
                listEl.innerHTML = '';
                for (const [key, c] of Object.entries(activeCriteria)) {
                    const li = document.createElement('li');
                    li.style.marginTop = '8px';
                    li.innerHTML = `<strong style="color: var(--accent-blue);">${esc(key)} (${esc(c.name)}):</strong> ${esc(c.definition)}`;
                    listEl.appendChild(li);
                }
            }
        } catch (e) {
            console.error("Failed to load criteria:", e);
        }
    }

    async function loadReviews() {
        reviewArea.innerHTML = '<div class="glass no-records-msg"><p>Loading records for review...</p></div>';
        try {
            const res = await fetch('/api/review');
            const data = await res.json();
            if (!data.reviews || data.reviews.length === 0) {
                reviewArea.innerHTML = '<div class="glass no-records-msg"><p>All matching records have already been reviewed! 🎉</p></div>';
                return;
            }
            reviewArea.innerHTML = '';
            data.reviews.forEach(item => {
                const clone = reviewTemplate.content.cloneNode(true);
                const card = clone.querySelector('.review-card');

                clone.querySelector('.record-id').textContent += item.record.id;
                clone.querySelector('.record-title').textContent = item.record.title;
                clone.querySelector('.record-abstract').textContent = item.record.abstract;

                const rationaleContainer = clone.getElementById('llm-rationale-container');
                if (rationaleContainer) {
                    rationaleContainer.removeAttribute('id');
                    const critResults = item.result.criteria || {};
                    for (const key of Object.keys(activeCriteria)) {
                        const critData = critResults[key] || {};
                        const score = critData.score !== undefined ? parseFloat(critData.score).toFixed(2) : 'N/A';
                        const rationale = critData.rationale || '-';
                        const d = document.createElement('div');
                        d.style.marginBottom = '12px';
                        d.innerHTML = `<strong>${esc(key)} Rationale (Score: <span style="color:var(--accent-blue);">${score}</span>):</strong> <span>${esc(rationale)}</span>`;
                        rationaleContainer.appendChild(d);
                    }
                }

                const btnInclude = clone.querySelector('.btn-include');
                const btnExcludeInit = clone.querySelector('.btn-exclude-init');
                const reviewActions = clone.querySelector('.review-actions');
                const excludeOptions = clone.querySelector('.exclude-options');

                btnInclude.addEventListener('click', () => submitReview(item.record.id, 'Include', 'None', card));
                btnExcludeInit.addEventListener('click', () => {
                    reviewActions.classList.add('hidden');
                    excludeOptions.classList.remove('hidden');
                });

                const exclusionContainer = clone.getElementById('exclude-reasons-container');
                if (exclusionContainer) {
                    exclusionContainer.removeAttribute('id');
                    const keys = Object.keys(activeCriteria);
                    for (const k of keys) {
                        const btn = document.createElement('button');
                        btn.className = 'btn-outline btn-ex';
                        btn.textContent = k;
                        btn.addEventListener('click', () => submitReview(item.record.id, 'Exclude', k, card));
                        exclusionContainer.appendChild(btn);
                    }
                    const btnMultiple = document.createElement('button');
                    btnMultiple.className = 'btn-outline btn-ex';
                    btnMultiple.textContent = keys.join('+');
                    btnMultiple.addEventListener('click', () => submitReview(item.record.id, 'Exclude', keys.join('+'), card));
                    exclusionContainer.appendChild(btnMultiple);
                }
                reviewArea.appendChild(clone);
            });
        } catch (e) {
            reviewArea.innerHTML = '<div class="glass no-records-msg"><p>Failed to load reviews.</p></div>';
        }
    }

    async function submitReview(recordId, decision, unmet, cardElement) {
        try {
            const res = await fetch(`/api/review/${encodeURIComponent(recordId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, unmet_criteria: unmet })
            });
            if (res.ok) {
                cardElement.style.opacity = '0';
                setTimeout(() => cardElement.remove(), 300);
            }
        } catch (e) {
            alert('Failed to save review');
        }
    }

    // ------------------------------------------------------------------
    // Report Logic
    // ------------------------------------------------------------------
    $('btn-download-report').addEventListener('click', () => { window.location.href = '/api/report'; });

    // ------------------------------------------------------------------
    // Chat
    // ------------------------------------------------------------------
    const chatWindow = $('chat-window');
    const chatInput = $('chat-input');
    const chatScope = $('chat-scope');
    let chatHistory = [];
    let chatBusy = false;

    async function loadChatScopes() {
        try {
            const data = await api('/api/chat/scope');
            const n = data.scopes[chatScope.value] ?? 0;
            $('chat-scope-count').textContent = `${n} record${n === 1 ? '' : 's'} in scope`;
            [...chatScope.options].forEach(o => {
                const base = o.textContent.replace(/ \(\d+\)$/, '');
                o.textContent = `${base} (${data.scopes[o.value] ?? 0})`;
            });
        } catch (e) { console.error(e); }
    }
    chatScope.addEventListener('change', loadChatScopes);

    // Minimal, safe markdown: paragraphs, bullets, **bold**, `code`, [record ids]
    function renderMarkdown(text) {
        const inline = (s) => esc(s)
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\[([A-Za-z0-9_:.\-\/]+)\]/g, '<span class="cite">[$1]</span>');
        const blocks = text.trim().split(/\n{2,}/);
        return blocks.map(b => {
            const lines = b.split('\n');
            if (lines.every(l => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
                return '<ul>' + lines.map(l => `<li>${inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, ''))}</li>`).join('') + '</ul>';
            }
            if (/^#{1,6}\s/.test(lines[0])) {
                return `<p><strong>${inline(lines[0].replace(/^#{1,6}\s/, ''))}</strong>` +
                    (lines.length > 1 ? '<br>' + lines.slice(1).map(inline).join('<br>') : '') + '</p>';
            }
            return `<p>${lines.map(inline).join('<br>')}</p>`;
        }).join('');
    }

    function appendChat(role, text, meta = '') {
        const div = document.createElement('div');
        div.className = `chat-msg ${role}`;
        div.innerHTML = `<div class="bubble">${role === 'user' ? esc(text) : renderMarkdown(text)}${meta ? `<div class="meta">${esc(meta)}</div>` : ''}</div>`;
        chatWindow.appendChild(div);
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div;
    }

    async function sendChat() {
        const q = chatInput.value.trim();
        if (!q || chatBusy) return;
        chatBusy = true;
        $('btn-chat-send').disabled = true;
        chatInput.value = '';
        appendChat('user', q);
        chatHistory.push({ role: 'user', content: q });
        const typing = appendChat('assistant', 'Thinking…');
        typing.classList.add('typing');
        try {
            const data = await postJSON('/api/chat', { messages: chatHistory, scope: chatScope.value });
            typing.remove();
            chatHistory.push({ role: 'assistant', content: data.reply });
            appendChat('assistant', data.reply, `${data.n_records} records in scope`);
        } catch (e) {
            typing.remove();
            chatHistory.pop();
            appendChat('error', 'Error: ' + e.message);
        } finally {
            chatBusy = false;
            $('btn-chat-send').disabled = false;
            chatInput.focus();
        }
    }
    $('chat-form').addEventListener('submit', (e) => { e.preventDefault(); sendChat(); });
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
    $('btn-chat-clear').addEventListener('click', () => {
        chatHistory = [];
        chatWindow.innerHTML = '<div class="chat-msg assistant"><div class="bubble">Conversation cleared. Ask away.</div></div>';
    });

    // ------------------------------------------------------------------
    // Help popups: "?" buttons (data-help="<topic>") explain a feature and
    // which PRISMA 2020 checklist items it supports. Static, trusted content.
    // ------------------------------------------------------------------
    const HELP_TOPICS = {
        strategy: {
            title: 'Search Strategy',
            body: `
<p>This tab turns your research question into <strong>concept blocks</strong> (groups of synonyms combined with OR) and one ready-to-paste <strong>Boolean query per database</strong>, each in that database's own syntax. The LLM drafts it; your edits are saved automatically to <code>search_strategy.json</code>, which the CLI reads too.</p>
<p>A systematic review must report the <em>full</em> search strategy for every database so that others can repeat the search. Keep the saved file, and note the date you ran each query.</p>
<ul>
<li>Edit the concept blocks yourself and press <em>Rebuild from concepts</em> to regenerate every database query without an LLM call; press <em>Refine with AI</em> (the same button, once a strategy exists) when you want the model to propose new synonyms or apply your feedback. <em>Start over</em> discards everything and generates again from the research question only.</li>
<li>Write date limits in the <em>Scope notes</em> ("2010 onwards", "2015-2024"): they are turned into a publication-year filter in the queries and in the API harvests, and they are exactly what item 7 asks you to report as limits.</li>
<li>Peer review of the search by a librarian or information specialist is recommended before you run it for real.</li>
</ul>`,
            items: '<strong>PRISMA 2020</strong> item 6 (information sources, with the date each was last searched) and item 7 (full search strategies for all databases, including any filters and limits). See also the PRISMA-S extension for reporting searches.',
        },
        rationale: {
            title: 'Rationale & limitations',
            body: `
<p>Two free-text notes the LLM writes alongside the strategy. They are <strong>not used by the software</strong>; they exist for your write-up and your audit trail.</p>
<ul>
<li><strong>Rationale</strong>: why these concepts and terms were chosen. Check it to confirm the model understood the question, then reuse it when you justify the search in your methods section or protocol.</li>
<li><strong>Limitations</strong>: known gaps of the search, for example synonyms not covered, databases without truncation, language or date restrictions. These belong in the limitations paragraph of your review.</li>
</ul>
<p>Both fields are editable and are saved with the strategy.</p>`,
            items: '<strong>PRISMA 2020</strong> item 7 (search strategy, so readers can judge its comprehensiveness) and item 23c (limitations of the review processes used). Documenting the reasoning behind the search also supports PRISMA-S.',
        },
        harvest: {
            title: 'Database queries & harvesting',
            body: `
<p>Each row is the query for one database. <strong>Run</strong> sends it to that database's official API (OpenAlex, Semantic Scholar, arXiv, Crossref need no key; Scopus and IEEE Xplore need a key) and stores the hits straight into the corpus as a <strong>harvest run</strong> that remembers the query, database, date, year range and how many records were new or duplicates. Any run can be downloaded as <code>.bib</code>. Databases without an open API (ACM DL, Web of Science) show the query to paste into their own search form; export the results from there and upload the file on <em>Ingestion</em>.</p>
<p>The stored runs give you, per database, the number of records retrieved and the date: the first box of the PRISMA flow diagram.</p>`,
            items: '<strong>PRISMA 2020</strong> item 6 (information sources), item 7 (search strategy) and item 16a (number of records identified from each source, for the flow diagram).',
        },
        ingest: {
            title: 'Data ingestion & deduplication',
            body: `
<p>Upload the BibTeX exports from your databases. Records are parsed, normalised and <strong>deduplicated</strong>: first by DOI, then by a title / year / first-author key, so the same paper found in several databases is screened once.</p>
<p>The counts shown here give you two flow-diagram numbers: records identified (all uploads) and duplicates removed before screening.</p>`,
            items: '<strong>PRISMA 2020</strong> item 16a (flow diagram: records identified, duplicate records removed before screening).',
        },
        criteria: {
            title: 'Inclusion criteria',
            body: `
<p>The criteria are the only place where your research field enters the tool. Each criterion has a <strong>definition</strong>, <strong>signals</strong> the model should look for, and <strong>negative indicators</strong> that must not be mistaken for evidence. They drive the screening prompt, the review buttons, the CSV columns and the chat context.</p>
<ul>
<li>Define them <em>before</em> screening and record them in your protocol; changing them mid-project means re-screening (use <em>Reset screening results</em>).</li>
<li>The LLM assistant can draft a set from the research question, but you own the final wording.</li>
</ul>`,
            items: '<strong>PRISMA 2020</strong> item 5 (eligibility criteria: inclusion and exclusion criteria for the review) and item 4 (protocol / registration, where the criteria should be fixed in advance).',
        },
        screening: {
            title: 'AI screening',
            body: `
<p>Each unique record (title + abstract) is sent to the LLM with your criteria. For every criterion the model returns a score, quoted evidence and a rationale, and an overall <strong>Include / Exclude / Maybe</strong>. The prompt favours precision: no explicit evidence, no inclusion.</p>
<p><strong>Decision strictness</strong> sets how readily a paper becomes <em>Maybe</em>: <em>Strict</em> keeps the review queue small (Maybe only for highly ambiguous but suggestive abstracts), <em>Balanced</em> sends every unclear abstract to review, <em>Lenient</em> excludes only clearly off-topic papers. Pick it before screening and report it; every result records the level, and one review cannot mix levels.</p>
<p>For reproducibility the screening model is <strong>pinned to one concrete model</strong>; every result stores the model that judged it, and all prompts and raw responses are logged to <code>logs/screening.log</code>.</p>
<p>When you report the review, state that an automation tool assisted title/abstract screening, name the model (see <em>model_version</em> in the CSV), and describe the human check that followed. The tool does not perform full-text eligibility assessment.</p>`,
            items: '<strong>PRISMA 2020</strong> item 8 (selection process: how records were screened, how many reviewers, and any automation tools used) and item 16a (records screened / excluded).',
        },
        review: {
            title: 'Human-in-the-loop review',
            body: `
<p>Records the model marked <strong>Maybe</strong> land here for a human decision. You see the abstract, the per-criterion scores, evidence and rationale, and decide Include or Exclude. Nothing is included in the final set without this step for uncertain cases.</p>
<p>Consider having a second reviewer check a sample of the AI's confident Include / Exclude decisions as well, and record disagreements and how they were resolved.</p>`,
            items: '<strong>PRISMA 2020</strong> item 8 (selection process, including whether reviewers worked independently) and item 16b (records excluded, with reasons, where applicable).',
        },
        report: {
            title: 'PRISMA reporting',
            body: `
<p>The CSV contains one row per screened record with the decision, and for each criterion its score, evidence and rationale, plus the model version. The summary counts (identified, duplicates removed, screened, included, excluded, pending review) feed the <strong>PRISMA 2020 flow diagram</strong>.</p>
<p>Remember that this tool covers identification and title/abstract screening only. Full-text retrieval and eligibility assessment, risk-of-bias appraisal and synthesis are separate steps you carry out and report yourself.</p>`,
            items: '<strong>PRISMA 2020</strong> item 16a (study selection results and flow diagram) and item 27 (availability of data and materials: the CSV and <code>screening.log</code> can be archived as supplementary material).',
        },
        chat: {
            title: 'Ask the corpus',
            body: `
<p>A chatbot that has read the included records (or, if you widen the scope, also the Maybe or all screened records) and answers questions about them, citing record IDs. Use it to spot themes, compare methods, or find which papers mention something.</p>
<p>It is an assistant for exploration, not a PRISMA stage: verify every claim against the full texts before it goes into your synthesis, and do not rely on it for eligibility decisions.</p>`,
            items: 'Supports the preparation of <strong>PRISMA 2020</strong> item 13 (synthesis methods) and item 20 (results of syntheses), but replaces neither.',
        },
    };

    const helpModal = $('help-modal');
    function openHelp(topic) {
        const t = HELP_TOPICS[topic];
        if (!t) return;
        $('help-title').textContent = t.title;
        $('help-body').innerHTML = t.body + `<div class="help-ref">${t.items}</div>`;
        helpModal.classList.remove('hidden');
        $('btn-help-ok').focus();
    }
    function closeHelp() { helpModal.classList.add('hidden'); }
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-help]');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();      // keep <summary> from toggling
        openHelp(btn.dataset.help);
    });
    $('btn-help-close').addEventListener('click', closeHelp);
    $('btn-help-ok').addEventListener('click', closeHelp);
    helpModal.addEventListener('click', (e) => { if (e.target === helpModal) closeHelp(); });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !helpModal.classList.contains('hidden')) closeHelp();
    });

    // ------------------------------------------------------------------
    // Settings dialog (LLM provider, models, theme) -> PUT /api/settings
    // ------------------------------------------------------------------
    const settingsModal = $('settings-modal');
    const THEME_KEY = 'prismaowl-theme';
    const SETTING_FIELDS = ['LLM_PROVIDER', 'ORCA_BASE_URL', 'GEMINI_BASE_URL', 'MODEL_NAME', 'MODEL_SCREENING',
        'MODEL_QUERY', 'MODEL_CRITERIA', 'MODEL_CHAT', 'MAX_RETRIES', 'LLM_TIMEOUT', 'OPENALEX_EMAIL'];
    const SECRET_FIELDS = ['ORCA_API_KEY', 'GEMINI_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY',
        'SCOPUS_API_KEY', 'SCOPUS_INST_TOKEN', 'IEEE_API_KEY'];
    const settingInput = (key) => $(`setting-${key}`);
    let settingsSnapshot = null;

    function applyTheme(choice) {
        let t = choice || 'dark';
        if (t === 'system') t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', t);
    }
    function savedTheme() {
        try { return localStorage.getItem(THEME_KEY) || 'dark'; } catch (_) { return 'dark'; }
    }
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
        if (savedTheme() === 'system') applyTheme('system');
    });
    $('setting-theme').addEventListener('change', (e) => {
        try { localStorage.setItem(THEME_KEY, e.target.value); } catch (_) { /* private mode */ }
        applyTheme(e.target.value);
    });

    function updateProviderBlocks() {
        const chosen = settingInput('LLM_PROVIDER').value;
        const snap = settingsSnapshot || { secrets: {}, providers: {} };
        const orcaSet = !!(snap.secrets.ORCA_API_KEY || {}).set;
        const gemSet = !!(snap.secrets.GEMINI_API_KEY || {}).set;
        // Mirror the server's auto-detect rule so the highlight matches what will run.
        const active = chosen || (orcaSet ? 'orcarouter' : (gemSet ? 'gemini' : 'orcarouter'));
        ['orcarouter', 'gemini'].forEach(pv => {
            $(`provider-block-${pv}`).classList.toggle('active', pv === active);
            const tag = $(`tag-${pv}`);
            const set = pv === 'orcarouter' ? orcaSet : gemSet;
            tag.textContent = pv === active ? 'active' : (set ? 'key saved' : 'no key');
            tag.className = 'tag ' + (pv === active ? 'ok' : (set ? '' : 'muted'));
        });
        $('setting-provider-help').textContent = chosen
            ? `Screening, query building, criteria and chat will all use ${snap.providers[chosen]?.label || chosen}.`
            : `Auto-detect picks OrcaRouter when its key is saved, otherwise Gemini. Currently: ${snap.providers[active]?.label || active}.`;
    }
    settingInput('LLM_PROVIDER').addEventListener('change', updateProviderBlocks);

    // OrcaRouter routing toggle: Auto <-> orcarouter/auto, Free <-> orcarouter/free.
    // Anything else typed into MODEL_NAME leaves both unselected ("custom").
    const ROUTING_MODELS = { auto: 'orcarouter/auto', free: 'orcarouter/free' };
    const isMetaModel = (id) => /^orcarouter\//i.test((id || '').trim());
    const isFreeModel = (id) => /(-|:)free$/i.test((id || '').trim());
    let modelListCache = null;      // ids from /api/settings/models, once loaded

    function syncRoutingFromModel() {
        const name = settingInput('MODEL_NAME').value.trim().toLowerCase();
        const mode = name === '' || name === ROUTING_MODELS.auto ? 'auto'
            : name === ROUTING_MODELS.free ? 'free' : '';
        document.querySelectorAll('input[name="orca-routing"]').forEach(r => { r.checked = r.value === mode; });
    }
    settingInput('MODEL_NAME').addEventListener('input', syncRoutingFromModel);

    async function loadModelList(provider) {
        if (modelListCache) return modelListCache;
        const res = await api('/api/settings/models' + (provider ? `?provider=${encodeURIComponent(provider)}` : ''));
        modelListCache = res;
        const dl = $('model-ids');
        dl.innerHTML = '';
        res.models.forEach(id => { const o = document.createElement('option'); o.value = id; dl.appendChild(o); });
        return res;
    }

    document.querySelectorAll('input[name="orca-routing"]').forEach(radio => radio.addEventListener('change', async () => {
        if (!radio.checked) return;
        settingInput('MODEL_NAME').value = ROUTING_MODELS[radio.value];
        const st = $('settings-models-status');
        const screening = settingInput('MODEL_SCREENING');
        if (radio.value === 'free') {
            // Screening cannot run on the meta-model: pick a concrete free model
            // unless the field already holds one.
            if (!isFreeModel(screening.value)) {
                let choice = (settingsSnapshot && settingsSnapshot.free_screening_default) || '';
                try {
                    setStatus(st, 'Looking up free models…');
                    const res = await loadModelList('orcarouter');
                    const free = res.models.filter(isFreeModel);
                    if (free.length) choice = free.includes(choice) ? choice : free[0];
                    setStatus(st, `Screening model set to ${choice} (${free.length} free models available).`, 'success');
                } catch (e) {
                    setStatus(st, `Could not load the model list (${e.message}); using ${choice}.`, 'error');
                }
                screening.value = choice;
            }
        } else if (isMetaModel(screening.value) || screening.value.trim() === '') {
            setStatus(st, 'Auto routing needs credits. Pick one concrete screening model below (meta-models are refused for screening).', '');
        } else {
            setStatus(st, '');
        }
    }));

    function renderSecret(key, state) {
        const input = settingInput(key);
        input.value = '';
        input.dataset.clear = '';
        input.classList.remove('cleared');
        input.placeholder = state && state.set
            ? `saved${state.hint ? ' (' + state.hint + ')' : ''} - leave blank to keep`
            : 'not set';
        const btn = document.querySelector(`[data-clear="${key}"]`);
        if (btn) btn.hidden = !(state && state.set);
    }
    document.querySelectorAll('[data-clear]').forEach(btn => btn.addEventListener('click', () => {
        const input = settingInput(btn.dataset.clear);
        const clearing = input.dataset.clear !== '1';
        input.dataset.clear = clearing ? '1' : '';
        input.value = '';
        input.classList.toggle('cleared', clearing);
        input.placeholder = clearing ? 'will be removed on save' : 'saved - leave blank to keep';
        btn.textContent = clearing ? 'Keep' : 'Remove';
        if (btn.dataset.clear === 'ORCA_API_KEY' || btn.dataset.clear === 'GEMINI_API_KEY') updateProviderBlocks();
    }));

    async function loadSettings() {
        setStatus($('settings-status'), 'Loading…');
        const data = await api('/api/settings');
        settingsSnapshot = data;
        SETTING_FIELDS.forEach(k => { settingInput(k).value = data.values[k] ?? ''; });
        SECRET_FIELDS.forEach(k => renderSecret(k, data.secrets[k]));
        document.querySelectorAll('[data-clear]').forEach(b => { b.textContent = 'Remove'; });
        $('setting-theme').value = savedTheme();
        updateProviderBlocks();
        syncRoutingFromModel();
        setStatus($('settings-status'), '');
        refreshPinWarning();
    }

    async function refreshPinWarning() {
        try {
            const st = await api('/api/screen/status');
            const warn = $('settings-pin-warning');
            warn.textContent = st.model_ok ? '' : st.model_error;
            warn.classList.toggle('hidden', !!st.model_ok);
        } catch (_) { /* ignore */ }
    }

    function collectSettings() {
        const body = {};
        SETTING_FIELDS.forEach(k => { body[k] = settingInput(k).value.trim(); });
        SECRET_FIELDS.forEach(k => {
            const input = settingInput(k);
            if (input.dataset.clear === '1') body[k] = '';
            else if (input.value.trim()) body[k] = input.value.trim();
            else body[k] = null;                      // keep the saved value
        });
        return body;
    }

    function openSettings() {
        settingsModal.classList.remove('hidden');
        modelListCache = null;
        $('setting-theme').value = savedTheme();
        setStatus($('settings-test-status'), '');
        setStatus($('settings-models-status'), '');
        loadSettings().catch(e => setStatus($('settings-status'), `Could not load settings: ${e.message}`, 'error'));
    }
    function closeSettings() { settingsModal.classList.add('hidden'); }

    $('btn-settings').addEventListener('click', openSettings);
    $('btn-settings-close').addEventListener('click', closeSettings);
    $('btn-settings-cancel').addEventListener('click', closeSettings);
    settingsModal.addEventListener('click', (e) => { if (e.target === settingsModal) closeSettings(); });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !settingsModal.classList.contains('hidden')) closeSettings();
    });

    $('btn-settings-save').addEventListener('click', async () => {
        const btn = $('btn-settings-save');
        btn.disabled = true;
        setStatus($('settings-status'), 'Saving…');
        try {
            await postJSON('/api/settings', collectSettings(), 'PUT');
            closeSettings();
            loadLLMInfo();
            updateScreenStats();
        } catch (e) {
            setStatus($('settings-status'), e.message, 'error');
        } finally {
            btn.disabled = false;
        }
    });

    $('btn-settings-test').addEventListener('click', async () => {
        const st = $('settings-test-status');
        setStatus(st, 'Testing saved key…');
        try {
            const res = await postJSON('/api/settings/test', { provider: settingInput('LLM_PROVIDER').value });
            setStatus(st, `${res.label} OK: ${res.model_count} models available.`, 'success');
        } catch (e) {
            setStatus(st, e.message, 'error');
        }
    });

    $('btn-settings-models').addEventListener('click', async () => {
        const st = $('settings-models-status');
        setStatus(st, 'Loading…');
        try {
            modelListCache = null;
            const res = await loadModelList(settingInput('LLM_PROVIDER').value);
            setStatus(st, `${res.models.length} models from ${res.label}; start typing in a field to pick one.`, 'success');
        } catch (e) {
            setStatus(st, e.message, 'error');
        }
    });

    applyTheme(savedTheme());
    // Deep links: #settings opens the dialog, #<tab id> opens that tab.
    const initialHash = location.hash.slice(1);
    if (initialHash === 'settings') openSettings();
    else if (initialHash && document.getElementById(initialHash)?.classList.contains('tab-pane')) showTab(initialHash);

    // ------------------------------------------------------------------
    // Initial Load
    // ------------------------------------------------------------------
    async function loadLLMInfo() {
        try {
            const data = await api('/api/llm');
            $('llm-model-label').textContent = data.configured
                ? `LLM: ${data.models.default} (${data.provider})`
                : `LLM: ${data.key_env || 'API key'} missing`;
            $('llm-model-label').title = [`provider: ${data.provider}`]
                .concat(Object.entries(data.models).map(([k, v]) => `${k}: ${v}`)).join('\n');
        } catch (_) { /* ignore */ }
    }

    loadCriteria().then(() => { updateScreenStats(); });
    loadLLMInfo();
    loadHarvestSources().then(loadStrategy).then(() => { loadHarvestRuns(); pollHarvestJobs(); });
    loadCriteriaEditor();
});
