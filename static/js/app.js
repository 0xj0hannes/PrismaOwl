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
        if (tabId === 'review') loadReviews();
        if (tabId === 'strategy') { loadHarvestSources().then(() => { loadHarvestFiles(); pollHarvestJobs(); }); }
        if (tabId === 'criteria') { if (!criteriaDirty) loadCriteriaEditor(); }
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
                uploadStatus.innerHTML = `<span class="success">Success! Loaded ${data.uploaded} records (Total Unique Database size: ${data.total_unique_db}).</span>`;
            } else {
                uploadStatus.innerHTML = `<span class="error">Error: ${esc(data.error)}</span>`;
            }
        } catch (err) {
            uploadStatus.innerHTML = `<span class="error">Network error.</span>`;
        }
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
        row.querySelector('.concept-remove').addEventListener('click', () => row.remove());
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
            if (savedStrategy && savedStrategy.research_question && !$('criteria-topic').value) {
                $('criteria-topic').placeholder = savedStrategy.research_question;
            }
        } catch (e) { console.error('Failed to load strategy', e); }
    }

    $('btn-add-concept').addEventListener('click', () => renderConcept());

    async function runStrategyAI(refine) {
        const status = $('strategy-ai-status');
        const topic = $('strategy-topic').value.trim();
        if (!topic && !(refine && savedStrategy)) { setStatus(status, 'Enter a research question first.', 'error'); return; }
        const btns = [$('btn-strategy-generate'), $('btn-strategy-refine')];
        btns.forEach(b => b.disabled = true);
        setStatus(status, refine ? 'Refining with the LLM… (can take a minute)' : 'Asking the LLM… (can take a minute)');
        try {
            const body = { topic, feedback: $('strategy-feedback').value.trim() };
            if (refine) body.current = collectStrategy();
            const data = await postJSON('/api/search-strategy/generate', body);
            renderStrategy(data.strategy);
            setStatus(status, 'Draft ready. Review, edit, then Save.', 'success');
            $('strategy-save-status').textContent = 'Unsaved changes';
        } catch (e) {
            setStatus(status, 'Error: ' + e.message, 'error');
        } finally { btns.forEach(b => b.disabled = false); }
    }
    $('btn-strategy-generate').addEventListener('click', () => runStrategyAI(false));
    $('btn-strategy-refine').addEventListener('click', () => runStrategyAI(true));

    $('btn-strategy-save').addEventListener('click', async () => {
        const status = $('strategy-save-status');
        try {
            const data = await postJSON('/api/search-strategy', collectStrategy(), 'PUT');
            savedStrategy = data.strategy;
            setStatus(status, 'Saved to search_strategy.json', 'success');
        } catch (e) { setStatus(status, 'Error: ' + e.message, 'error'); }
    });
    $('btn-strategy-reload').addEventListener('click', () => { loadStrategy(); setStatus($('strategy-save-status'), ''); });

    // --- Harvesting -----------------------------------------------------
    let harvestPoll = null;

    async function startHarvest(source, query, btn) {
        if (!query.trim()) { alert('Query is empty.'); return; }
        btn.disabled = true;
        try {
            await postJSON('/api/harvest', { source, query, max_results: parseInt($('harvest-max').value || '500', 10) });
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
                } else if (j.status === 'done') {
                    right = `<span class="tag ok">done · ${j.result.count} records (${j.result.with_abstract} with abstracts)</span>`;
                } else {
                    right = `<span class="tag warn" title="${esc(j.error)}">failed: ${esc((j.error || '').slice(0, 120))}</span>`;
                }
                div.innerHTML = `<div><strong>${esc(label)}</strong> <span class="meta">${esc(j.started.replace('T', ' '))}</span><br>
                                 <span class="meta mono">${esc(j.query.slice(0, 160))}${j.query.length > 160 ? '…' : ''}</span></div>${right}`;
                box.appendChild(div);
            });
            if (anyRunning) {
                if (!harvestPoll) harvestPoll = setInterval(pollHarvestJobs, 2000);
            } else if (harvestPoll) {
                clearInterval(harvestPoll); harvestPoll = null; loadHarvestFiles();
            }
        } catch (e) { console.error(e); }
    }

    async function loadHarvestFiles() {
        const box = $('harvest-files');
        try {
            const data = await api('/api/harvest/files');
            if (!data.files.length) { box.innerHTML = '<p class="info-text">No harvested files yet. Run a query above.</p>'; return; }
            box.innerHTML = '';
            data.files.forEach(f => {
                const div = document.createElement('div');
                div.className = 'harvest-file';
                div.innerHTML = `<div><strong>${esc(f.name)}</strong><br><span class="meta">${(f.size / 1024).toFixed(1)} KB · ${esc(f.modified.replace('T', ' '))}</span></div>
                    <div class="btn-row">
                        <a class="btn-outline btn-sm" href="/api/harvest/download/${encodeURIComponent(f.name)}" style="text-decoration:none">⬇ Download .bib</a>
                        <button class="btn-success btn-sm f-ingest">Ingest into corpus</button>
                        <button class="btn-outline btn-sm f-delete">✕</button>
                        <span class="status-inline f-status"></span>
                    </div>`;
                div.querySelector('.f-ingest').addEventListener('click', async (e) => {
                    e.target.disabled = true;
                    const st = div.querySelector('.f-status');
                    setStatus(st, 'Ingesting…');
                    try {
                        const r = await postJSON(`/api/harvest/ingest/${encodeURIComponent(f.name)}`, {});
                        setStatus(st, `Added ${r.uploaded} (corpus now ${r.total_unique_db} unique)`, 'success');
                    } catch (err) { setStatus(st, 'Error: ' + err.message, 'error'); }
                    finally { e.target.disabled = false; }
                });
                div.querySelector('.f-delete').addEventListener('click', async () => {
                    if (!confirm(`Delete ${f.name}?`)) return;
                    try { await api(`/api/harvest/files/${encodeURIComponent(f.name)}`, { method: 'DELETE' }); loadHarvestFiles(); }
                    catch (err) { alert(err.message); }
                });
                box.appendChild(div);
            });
        } catch (e) { box.innerHTML = '<p class="error">Failed to list harvested files.</p>'; }
    }

    // ------------------------------------------------------------------
    // Criteria editor
    // ------------------------------------------------------------------
    const criteriaEditor = $('criteria-editor');
    const criterionTemplate = $('criterion-template');
    let criteriaDirty = false;

    function markCriteriaDirty(dirty) {
        criteriaDirty = dirty;
        $('criteria-draft-badge').classList.toggle('hidden', !dirty);
    }

    function renderCriterion(key = '', c = {}) {
        const clone = criterionTemplate.content.cloneNode(true);
        const card = clone.querySelector('.criterion-card');
        card.querySelector('.crit-key').value = key;
        card.querySelector('.crit-name').value = c.name || '';
        card.querySelector('.crit-definition').value = c.definition || '';
        card.querySelector('.crit-signals').value = c.signals || '';
        card.querySelector('.crit-negative').value = c.negative_indicators || '';
        card.querySelector('.crit-remove').addEventListener('click', () => { card.remove(); markCriteriaDirty(true); });
        card.querySelectorAll('input, textarea').forEach(el => el.addEventListener('input', () => markCriteriaDirty(true)));
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
            markCriteriaDirty(false);
        } catch (e) { console.error(e); }
    }

    $('btn-add-criterion').addEventListener('click', () => {
        const n = criteriaEditor.querySelectorAll('.criterion-card').length + 1;
        renderCriterion(`IC${n}`, {});
        markCriteriaDirty(true);
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
            markCriteriaDirty(true);
            setStatus(status, 'Draft ready. Edit, then Save.', 'success');
        } catch (e) { setStatus(status, 'Error: ' + e.message, 'error'); }
        finally { btns.forEach(b => b.disabled = false); }
    }
    $('btn-criteria-draft').addEventListener('click', () => runCriteriaAI(false));
    $('btn-criteria-refine').addEventListener('click', () => runCriteriaAI(true));

    $('btn-criteria-save').addEventListener('click', async () => {
        const status = $('criteria-save-status');
        try {
            await postJSON('/api/criteria', collectCriteria(), 'PUT');
            markCriteriaDirty(false);
            setStatus(status, 'Saved to criteria.json', 'success');
            loadCriteria();
        } catch (e) { setStatus(status, 'Error: ' + e.message, 'error'); }
    });
    $('btn-criteria-revert').addEventListener('click', () => { loadCriteriaEditor(); setStatus($('criteria-save-status'), ''); });

    $('btn-reset-results').addEventListener('click', async () => {
        if (!confirm('Delete ALL screening results (including human review decisions)? Records are kept.')) return;
        const st = $('reset-status');
        try {
            const r = await api('/api/screen/results', { method: 'DELETE' });
            setStatus(st, `Deleted ${r.deleted} results.`, 'success');
        } catch (e) { setStatus(st, 'Error: ' + e.message, 'error'); }
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
    loadHarvestSources().then(loadStrategy).then(() => { loadHarvestFiles(); pollHarvestJobs(); });
    loadCriteriaEditor();
});
