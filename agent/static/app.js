/* ═══ STATE ═══ */
let selectedMode = 'base';
let reviewHistory = [];
let currentPromptMode = 'base';
let promptsCache = {};
let modalPRNumber = null;
let activeJobId = null;
let chatPRNumber = null;

/* ═══ UTILS ═══ */
function relativeTime(dateStr) {
    if (!dateStr) return '';
    return dayjs(dateStr).fromNow();
}

/** Escapes HTML special characters to prevent XSS (F15). */
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatTokenCount(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

const _TOAST_BG = { error: '#f87171', success: '#34d399', warn: '#fbbf24' };
function showAlert(id, msg, type = 'error') {
    Toastify({
        text: msg, duration: 4000, gravity: 'top', position: 'right',
        style: {
            background: _TOAST_BG[type] || _TOAST_BG.error,
            color: '#080b12', borderRadius: '6px', fontSize: '13px',
            fontFamily: 'Inter, system-ui, sans-serif', fontWeight: '600',
            boxShadow: '0 4px 16px rgba(0,0,0,.5)',
        },
        stopOnFocus: true,
    }).showToast();
}

function addSuggestion(t) { const ta = document.getElementById('instructions'); ta.value = ta.value ? ta.value + '\n' + t : t; ta.focus(); }

/* ═══ INIT ═══ */
(async function () {
    const savedTheme = localStorage.getItem('theme') || 'light';
    setTheme(savedTheme);
    document.getElementById('themeToggle').addEventListener('click', () => {
        const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        setTheme(newTheme);
    });

    dayjs.extend(window.dayjs_plugin_relativeTime);

    tippy('.mode-btn[data-mode="base"]', { content: 'General code quality review', placement: 'bottom' });
    tippy('.mode-btn[data-mode="security"]', { content: 'Security flaws and vulnerabilities', placement: 'bottom' });
    tippy('.mode-btn[data-mode="performance"]', { content: 'Performance and optimization analysis', placement: 'bottom' });
    tippy('.mode-btn[data-mode="multi-agent"]', { content: 'Security + Performance + Style agents run in parallel; a Lead Agent consolidates the results', placement: 'bottom' });
    tippy('#loadPRsBtn', { content: 'Fetch open PRs from GitHub', placement: 'bottom' });
    tippy('[data-page="review"]', { content: 'Start a code review', placement: 'right' });
    tippy('[data-page="board"]', { content: 'Kanban board view', placement: 'right' });
    tippy('[data-page="settings"]', { content: 'API connections and configuration', placement: 'right' });
    tippy('[data-page="prompts"]', { content: 'Edit prompt templates', placement: 'right' });
    loadHistory();
    updateExternalLinks();
    const pg = document.querySelector('.nav-item.active')?.dataset.page;
    if (pg === 'settings') { loadSettings(); loadWebhookStatus(); }
    if (pg === 'prompts') loadPrompts();
    if (pg === 'board') loadBoard();
})();

/* ═══ THEME ═══ */
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    const sunIcon = document.getElementById('themeIconSun');
    const moonIcon = document.getElementById('themeIconMoon');
    if (theme === 'dark') {
        sunIcon.style.display = 'block';
        moonIcon.style.display = 'none';
    } else {
        sunIcon.style.display = 'none';
        moonIcon.style.display = 'block';
    }
}

/* ═══ NAV ═══ */
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
        e.preventDefault();
        const pg = item.dataset.page;
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');
        document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
        document.getElementById(`page-${pg}`).style.display = 'flex';
        history.pushState({}, '', item.href);
        if (pg === 'settings') { loadSettings(); loadWebhookStatus(); }
        if (pg === 'prompts') loadPrompts();
        if (pg === 'board') loadBoard();
    });
});

/* ═══ EXTERNAL LINKS ═══ */
async function updateExternalLinks() {
    try {
        const data = await (await fetch('/api/settings')).json();
        const ghRepo = data.api.github_repo;
        const jiraUrl = data.api.jira_url;
        if (ghRepo && !ghRepo.includes('•')) document.getElementById('linkGithub').href = `https://github.com/${ghRepo}`;
        if (jiraUrl && !jiraUrl.includes('•')) document.getElementById('linkJira').href = jiraUrl;
        document.getElementById('activeRepoName').textContent = ghRepo && !ghRepo.includes('•') ? ghRepo : 'No repo selected';
    } catch (e) { }
}
/* ═══ MODE ═══ */
document.querySelectorAll('.mode-btn').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.mode-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); selectedMode = b.dataset.mode;
}));

/* ═══ OPEN PRS ═══ */
async function loadOpenPRs() {
    const btn = document.getElementById('loadPRsBtn'); btn.disabled = true;
    try {
        const data = await (await fetch('/api/github/pulls')).json();
        const bar = document.getElementById('openPRs');
        // inner wrapper — the label already lives in the static HTML
        let wrap = bar.querySelector('.pr-chips-wrap');
        if (!wrap) { wrap = document.createElement('div'); wrap.className = 'pr-chips-wrap'; bar.appendChild(wrap); }
        if (data.success && data.pulls.length > 0) {
            bar.style.display = 'flex';
            wrap.innerHTML = data.pulls.map(pr => `
                        <button class="pr-chip" onclick="selectPR(${pr.number}, this)">
                            <span class="pr-chip-num">#${pr.number}</span>
                            <span class="pr-chip-title">${escapeHtml(pr.title.substring(0, 40))}${pr.title.length > 40 ? '...' : ''}</span>
                            <span class="pr-chip-branch">${escapeHtml(pr.head)} → ${escapeHtml(pr.base)}</span>
                        </button>`).join('');
        } else if (data.success) {
            bar.style.display = 'flex'; wrap.innerHTML = '<span class="text-muted" style="padding:8px 0">No open PRs found</span>';
        } else { showAlert('reviewAlert', data.error); }
    } catch (e) { showAlert('reviewAlert', e.message); }
    finally { btn.disabled = false; }
}

function selectPR(num, el) {
    document.getElementById('prNumber').value = num;
    document.querySelectorAll('.pr-chip').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
}

/* ═══ REVIEW ═══ */
document.getElementById('reviewForm').addEventListener('submit', async e => {
    e.preventDefault();
    const prNumber = document.getElementById('prNumber').value;
    const instructions = document.getElementById('instructions').value;
    const btn = document.getElementById('submitBtn');
    if (!prNumber) { showAlert('reviewAlert', 'PR number is required.'); return; }

    btn.classList.add('loading'); btn.disabled = true;
    document.querySelector('.status-text').textContent = 'Starting Review...';
    document.querySelector('.status-dot').classList.add('working');

    try {
        const res = await fetch('/api/review', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pr_number: parseInt(prNumber), mode: selectedMode, pm_instructions: instructions || null })
        });
        const data = await res.json();
        if (data.success && data.job_id) {
            activeJobId = data.job_id;
            btn.style.display = 'none';
            document.getElementById('stopBtn').style.display = '';
            pollReviewStatus(data.job_id);
        } else {
            showAlert('reviewAlert', data.error || 'Something went wrong.');
            btn.classList.remove('loading'); btn.disabled = false;
            document.querySelector('.status-text').textContent = 'Error';
            document.querySelector('.status-dot').classList.remove('working');
        }
    } catch (e) {
        showAlert('reviewAlert', e.message);
        btn.classList.remove('loading'); btn.disabled = false;
    }
});

function _resetReviewButtons() {
    const btn = document.getElementById('submitBtn');
    const stopBtn = document.getElementById('stopBtn');
    btn.classList.remove('loading'); btn.disabled = false; btn.style.display = '';
    stopBtn.style.display = 'none'; stopBtn.disabled = false;
    activeJobId = null;
}

async function pollReviewStatus(jobId) {
    const statusText = document.querySelector('.status-text');
    const statusDot = document.querySelector('.status-dot');

    try {
        const res = await fetch(`/api/review/status/${jobId}`);
        const data = await res.json();

        if (data.status === 'completed') {
            document.getElementById('placeholder').style.display = 'none';
            const c = document.getElementById('reviewContent');
            const reviewText = (data.result && data.result.review) ? data.result.review : "Could not retrieve the review content.";
            c.style.display = 'block'; c.innerHTML = marked.parse(reviewText);
            c.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));

            // Show token usage for this review
            if (data.result && data.result.usage_stats) {
                const usage = data.result.usage_stats;
                const totalTokens = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
                const badge = document.getElementById('resultTokenBadge');
                document.getElementById('resultTokenCount').textContent = formatTokenCount(totalTokens) + ' token';
                badge.style.display = 'flex';
            }

            // Show chat panel
            const prNum = data.result?.pr_number || document.getElementById('prNumber').value;
            showChatPanel(prNum);

            showAlert('reviewAlert', 'Review completed.', 'success');
            loadHistory();
            _resetReviewButtons();
            statusText.textContent = 'Ready';
            statusDot.classList.remove('working');
        } else if (data.status === 'error') {
            showAlert('reviewAlert', data.error);
            _resetReviewButtons();
            statusText.textContent = 'Error';
            statusDot.classList.remove('working');
        } else if (data.status === 'cancelled') {
            showAlert('reviewAlert', 'Review cancelled.', 'warn');
            _resetReviewButtons();
            statusText.textContent = 'Cancelled';
            statusDot.classList.remove('working');
        } else {
            statusText.textContent = selectedMode === 'multi-agent' ? 'Multi-Agent Reviewing...' : 'AI Reviewing...';
            setTimeout(() => pollReviewStatus(jobId), 2000);
        }
    } catch (e) {
        console.error("Poll error:", e);
        setTimeout(() => pollReviewStatus(jobId), 3000);
    }
}

async function cancelReview() {
    if (!activeJobId) return;
    const stopBtn = document.getElementById('stopBtn');
    stopBtn.disabled = true;
    try {
        await fetch(`/api/review/cancel/${activeJobId}`, { method: 'POST' });
    } catch (e) { stopBtn.disabled = false; }
}

/* ═══ CHAT ═══ */
function showChatPanel(prNumber) {
    chatPRNumber = parseInt(prNumber);
    document.getElementById('chatPanel').style.display = '';
    document.getElementById('chatPRNumber').textContent = `#${chatPRNumber}`;
}

document.getElementById('chatInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
    }
});

async function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message || !chatPRNumber) return;

    const messagesEl = document.getElementById('chatMessages');
    // Remove welcome message
    const welcome = messagesEl.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Add user message
    const userMsg = document.createElement('div');
    userMsg.className = 'chat-msg chat-msg-user';
    userMsg.innerHTML = `<div class="chat-msg-content">${escapeHtml(message)}</div>`;
    messagesEl.appendChild(userMsg);

    input.value = '';
    input.disabled = true;
    document.getElementById('chatSendBtn').disabled = true;

    // Add loading indicator
    const loadingMsg = document.createElement('div');
    loadingMsg.className = 'chat-msg chat-msg-ai';
    loadingMsg.innerHTML = '<div class="chat-msg-content"><div class="chat-typing"><span></span><span></span><span></span></div></div>';
    messagesEl.appendChild(loadingMsg);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pr_number: chatPRNumber, message: message })
        });
        const data = await res.json();
        loadingMsg.remove();

        const aiMsg = document.createElement('div');
        aiMsg.className = 'chat-msg chat-msg-ai';
        if (data.success) {
            aiMsg.innerHTML = `<div class="chat-msg-content">${marked.parse(data.reply)}</div>`;
        } else {
            aiMsg.innerHTML = `<div class="chat-msg-content chat-error">Error: ${escapeHtml(data.error)}</div>`;
        }
        messagesEl.appendChild(aiMsg);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        // Highlight code blocks in AI response
        aiMsg.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    } catch (e) {
        loadingMsg.remove();
        const errMsg = document.createElement('div');
        errMsg.className = 'chat-msg chat-msg-ai';
        errMsg.innerHTML = `<div class="chat-msg-content chat-error">Connection error: ${escapeHtml(e.message)}</div>`;
        messagesEl.appendChild(errMsg);
    } finally {
        input.disabled = false;
        document.getElementById('chatSendBtn').disabled = false;
        input.focus();
    }
}

/* ═══ HISTORY ═══ */
async function loadHistory() {
    try { reviewHistory = await (await fetch('/api/history')).json(); renderHistory(); } catch (e) { }
}
function renderHistory() {
    const list = document.getElementById('historyList');
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!reviewHistory.length) {
        const el = document.createElement('div');
        el.className = 'empty-state';
        el.innerHTML = '<p>No reviews yet</p>';
        list.appendChild(el);
        return;
    }
    reviewHistory.forEach((item, i) => {
        const div = document.createElement('div');
        div.className = 'history-item';
        div.onclick = () => showHistoryItem(i);

        const modeLabels = {
            'base': 'General',
            'security': 'Security',
            'performance': 'Performance',
            'multi-agent': 'Multi-Agent',
            'multi-agent-linter': 'Linter',
            'linter': 'Linter'
        };
        const modeLabel = modeLabels[item.mode] || item.mode;
        const badgeClass = item.mode === 'multi-agent' ? 'hi-badge-multi' : `hi-badge-${item.mode}`;

        // Token info
        const tokenInfo = item.usage_stats
            ? `<span class="hi-tokens">${formatTokenCount((item.usage_stats.prompt_tokens || 0) + (item.usage_stats.completion_tokens || 0))} tok</span>`
            : '';

        div.innerHTML = `
                    <div class="hi-top"><span class="hi-pr">PR #${item.pr_number}</span><span class="hi-badge ${badgeClass}">${modeLabel}</span>${tokenInfo}</div>
                    <span class="hi-time">${relativeTime(item.timestamp)}</span>`;
        list.appendChild(div);
    });
}
function showHistoryItem(i) {
    const item = reviewHistory[i]; if (!item) return;
    document.getElementById('placeholder').style.display = 'none';
    const c = document.getElementById('reviewContent');
    const reviewText = item.review || "The review content is empty.";
    c.style.display = 'block'; c.innerHTML = marked.parse(reviewText);
    c.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));

    // Show token badge
    if (item.usage_stats) {
        const total = (item.usage_stats.prompt_tokens || 0) + (item.usage_stats.completion_tokens || 0);
        const badge = document.getElementById('resultTokenBadge');
        document.getElementById('resultTokenCount').textContent = formatTokenCount(total) + ' token';
        badge.style.display = 'flex';
    } else {
        document.getElementById('resultTokenBadge').style.display = 'none';
    }

    // Show chat panel for this PR
    showChatPanel(item.pr_number);
}

/* ═══ BOARD ═══ */
async function loadBoard() {
    const cols = { open: 'colOpen', in_review: 'colReview', merged: 'colMerged', closed: 'colClosed' };
    Object.values(cols).forEach(id => { document.getElementById(id).innerHTML = '<div class="col-loading">Loading...</div>'; });
    try {
        const data = await (await fetch('/api/board/columns')).json();
        if (!data.success) { showAlert('boardAlert', data.error); return; }
        const c = data.columns;
        document.getElementById('colCountOpen').textContent = c.open.length;
        document.getElementById('colCountReview').textContent = c.in_review.length;
        document.getElementById('colCountMerged').textContent = c.merged.length;
        document.getElementById('colCountClosed').textContent = c.closed.length;

        document.getElementById('colOpen').innerHTML = c.open.length ? c.open.map(renderBoardCard).join('') : '<div class="col-empty">No PRs</div>';
        document.getElementById('colReview').innerHTML = c.in_review.length ? c.in_review.map(renderBoardCard).join('') : '<div class="col-empty">No PRs</div>';
        document.getElementById('colMerged').innerHTML = c.merged.length ? c.merged.map(renderBoardCard).join('') : '<div class="col-empty">No PRs</div>';
        document.getElementById('colClosed').innerHTML = c.closed.length ? c.closed.map(renderBoardCard).join('') : '<div class="col-empty">No PRs</div>';
    } catch (e) { showAlert('boardAlert', e.message); }
}

function renderBoardCard(pr) {
    const labels = pr.labels.map(l => `<span class="board-label">${escapeHtml(l)}</span>`).join('');
    return `<div class="board-card" onclick="openPRModal(${pr.number})">
                <div class="bc-top"><span class="bc-num">#${pr.number}</span>${labels}</div>
                <div class="bc-title">${escapeHtml(pr.title)}</div>
                <div class="bc-meta">
                    ${pr.user_avatar ? `<img class="avatar-sm" src="${escapeHtml(pr.user_avatar)}" alt="${escapeHtml(pr.user)}">` : ''}
                    <span>${escapeHtml(pr.user)}</span>
                    <span class="bc-branch">${escapeHtml(pr.head)} → ${escapeHtml(pr.base)}</span>
                </div>
                <div class="bc-stats">
                    <span class="bc-add">+${pr.additions || 0}</span>
                    <span class="bc-del">-${pr.deletions || 0}</span>
                    <span class="bc-files">${pr.changed_files || 0} files</span>
                    <span class="bc-time">${relativeTime(pr.updated_at || pr.created_at)}</span>
                </div>
            </div>`;
}

/* ═══ PR MODAL ═══ */
let _diffLoaded = false;
let _diffVisible = false;

async function togglePRDiff() {
    const btn = document.getElementById('modalDiffBtn');
    const container = document.getElementById('modalDiff');
    _diffVisible = !_diffVisible;
    if (!_diffVisible) { container.style.display = 'none'; btn.textContent = 'Show Diff'; return; }
    btn.textContent = 'Hide Diff';
    if (_diffLoaded) { container.style.display = 'block'; return; }
    container.style.display = 'block';
    document.getElementById('modalDiffContent').innerHTML = '<div class="col-loading">Loading diff...</div>';
    try {
        const data = await (await fetch(`/api/github/pr/${modalPRNumber}/diff`)).json();
        const el = document.getElementById('modalDiffContent');
        if (!data.success) { el.textContent = data.error; return; }
        if (!data.diff) { el.innerHTML = '<div class="col-empty">Diff not found</div>'; return; }
        el.innerHTML = '';
        const ui = new Diff2HtmlUI(el, data.diff,
            { drawFileList: true, matching: 'lines', outputFormat: 'line-by-line', highlight: true }, hljs);
        ui.draw();
        _diffLoaded = true;
    } catch (e) { document.getElementById('modalDiffContent').textContent = e.message; }
}

async function openPRModal(num) {
    modalPRNumber = num;
    _diffLoaded = false; _diffVisible = false;
    document.getElementById('modalDiff').style.display = 'none';
    document.getElementById('modalDiffContent').innerHTML = '';
    document.getElementById('modalDiffBtn').textContent = 'Show Diff';
    document.getElementById('prModal').style.display = 'flex';
    document.getElementById('modalTitle').textContent = `PR #${num}`;
    document.getElementById('modalBody').innerHTML = '<div class="col-loading">Loading...</div>';
    try {
        const data = await (await fetch(`/api/github/pr/${num}`)).json();
        if (!data.success) { document.getElementById('modalBody').textContent = data.error; return; }
        const pr = data.pr;
        document.getElementById('modalGHLink').href = pr.url;
        document.getElementById('modalTitle').textContent = `#${pr.number} ${pr.title}`;
        let html = `<div class="modal-pr-header">
                    <div class="modal-pr-meta">${pr.user_avatar ? `<img class="avatar" src="${pr.user_avatar}" alt="${pr.user}">` : ''}
                        <span><strong>${pr.user}</strong> ${pr.head} → ${pr.base}</span>
                        <span class="modal-pr-date">${relativeTime(pr.created_at)}</span>
                    </div>
                    <div class="modal-pr-stats">
                        <span class="bc-add">+${pr.additions}</span> <span class="bc-del">-${pr.deletions}</span> <span class="bc-files">${pr.changed_files} files</span>
                    </div>
                </div>`;
        if (pr.body) html += `<div class="modal-pr-desc">${marked.parse(pr.body)}</div>`;
        if (pr.labels.length) html += `<div class="modal-labels">${pr.labels.map(l => `<span class="board-label">${l}</span>`).join('')}</div>`;
        html += `<h4 class="section-label" style="margin-top:16px">Changed Files</h4><div class="file-list">`;
        for (const f of pr.files) {
            html += `<div class="file-item"><span class="file-name">${f.filename}</span><span class="file-stat"><span class="bc-add">+${f.additions}</span><span class="bc-del">-${f.deletions}</span></span></div>`;
        }
        html += '</div>';
        if (pr.reviews.length) {
            html += '<h4 class="section-label" style="margin-top:16px">Reviews</h4><div class="review-list-modal">';
            for (const r of pr.reviews) {
                const stateClass = r.state === 'APPROVED' ? 'approved' : r.state === 'CHANGES_REQUESTED' ? 'changes' : 'commented';
                html += `<div class="review-item-modal"><span class="review-state ${stateClass}">${r.state}</span><span>${r.user}</span><span class="text-muted">${relativeTime(r.submitted_at)}</span></div>`;
            }
            html += '</div>';
        }
        document.getElementById('modalBody').innerHTML = html;
    } catch (e) { document.getElementById('modalBody').textContent = e.message; }
}

function closePRModal() {
    document.getElementById('prModal').style.display = 'none';
    _diffLoaded = false; _diffVisible = false;
    document.getElementById('modalDiff').style.display = 'none';
    document.getElementById('modalDiffContent').innerHTML = '';
    document.getElementById('modalDiffBtn').textContent = 'Show Diff';
}

function reviewFromModal() {
    if (!modalPRNumber) return;
    closePRModal();
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector('[data-page="review"]').classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
    document.getElementById('page-review').style.display = 'flex';
    document.getElementById('prNumber').value = modalPRNumber;
    history.pushState({}, '', '/');
}

/* ═══ REPO SELECTOR ═══ */
async function loadRepoList() {
    const container = document.getElementById('repoList');
    container.style.display = 'block';
    container.innerHTML = '<div class="col-loading">Loading repositories...</div>';
    try {
        const data = await (await fetch('/api/github/repos')).json();
        if (!data.success) { container.innerHTML = `<div class="text-muted">${data.error}</div>`; return; }
        container.innerHTML = data.repos.map(r => `
                    <div class="repo-item ${r.full_name === data.current ? 'repo-item-active' : ''}" onclick="switchRepo('${r.full_name}')">
                        <div class="repo-item-top">
                            <span class="repo-item-name">${r.full_name}</span>
                            ${r.private ? '<span class="repo-badge">Private</span>' : ''}
                        </div>
                        <div class="repo-item-meta">
                            ${r.language ? `<span>${r.language}</span>` : ''}
                            ${r.description ? `<span class="text-muted">${r.description.substring(0, 60)}</span>` : ''}
                        </div>
                    </div>`).join('');
    } catch (e) { container.innerHTML = `<div class="text-muted">${e.message}</div>`; }
}

async function switchRepo(name) {
    try {
        const data = await (await fetch('/api/github/switch-repo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: name }) })).json();
        if (data.success) {
            document.getElementById('s_github_repo').value = name;
            document.getElementById('activeRepoName').textContent = name;
            document.getElementById('repoList').style.display = 'none';
            showAlert('settingsAlert', `Active repo: ${name}`, 'success');
            updateExternalLinks();
        } else showAlert('settingsAlert', data.error);
    } catch (e) { showAlert('settingsAlert', e.message); }
}

/* ═══ SETTINGS ═══ */
async function loadSettings() {
    try {
        const data = await (await fetch('/api/settings')).json();
        document.getElementById('s_gemini_api_key').value = data.api.gemini_api_key || '';
        document.getElementById('s_github_token').value = data.api.github_token || '';
        document.getElementById('s_github_repo').value = data.api.github_repo || '';
        document.getElementById('s_jira_url').value = data.api.jira_url || '';
        document.getElementById('s_jira_email').value = data.api.jira_email || '';
        document.getElementById('s_jira_api_token').value = data.api.jira_api_token || '';
        document.getElementById('s_jira_review_status').value = data.api.jira_review_status || 'In Review';
        document.getElementById('s_provider').value = data.review.provider || 'ollama';
        document.getElementById('s_ollama_model').value = data.review.ollama_model || 'qwen2.5:7b';
        document.getElementById('s_ollama_base_url').value = data.review.ollama_base_url || 'http://localhost:11434';
        document.getElementById('s_model').value = data.review.model || 'gemini-2.0-flash';
        document.getElementById('s_fallback_model').value = data.review.fallback_model ?? 'gemini-1.5-flash';
        document.getElementById('s_max_tokens').value = data.review.max_tokens || 2048;
        document.getElementById('s_temperature').value = data.review.temperature || 0.3;
        document.getElementById('tempValue').textContent = data.review.temperature || 0.3;
        document.getElementById('s_default_mode').value = data.review.default_mode || 'base';
        toggleProviderFields();
    } catch (e) { showAlert('settingsAlert', e.message); }
}

function toggleProviderFields() {
    const p = document.getElementById('s_provider')?.value;
    const ollama = document.getElementById('ollama-fields');
    const gemini = document.getElementById('gemini-fields');
    if (ollama) ollama.style.display = p === 'gemini' ? 'none' : '';
    if (gemini) gemini.style.display = p === 'gemini' ? '' : 'none';
}

async function saveSettings() {
    try {
        const data = await (await fetch('/api/settings', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
                api: { gemini_api_key: document.getElementById('s_gemini_api_key').value, github_token: document.getElementById('s_github_token').value, github_repo: document.getElementById('s_github_repo').value, jira_url: document.getElementById('s_jira_url').value, jira_email: document.getElementById('s_jira_email').value, jira_api_token: document.getElementById('s_jira_api_token').value, jira_review_status: document.getElementById('s_jira_review_status').value },
                review: { provider: document.getElementById('s_provider').value, ollama_model: document.getElementById('s_ollama_model').value, ollama_base_url: document.getElementById('s_ollama_base_url').value, model: document.getElementById('s_model').value, fallback_model: document.getElementById('s_fallback_model').value, max_tokens: parseInt(document.getElementById('s_max_tokens').value), temperature: parseFloat(document.getElementById('s_temperature').value), default_mode: document.getElementById('s_default_mode').value }
            })
        })).json();
        if (data.success) { showAlert('settingsAlert', 'Settings saved.', 'success'); updateExternalLinks(); } else showAlert('settingsAlert', data.message);
    } catch (e) { showAlert('settingsAlert', e.message); }
}

async function checkConnections() {
    const c = document.getElementById('connectionStatus');
    c.innerHTML = ['Gemini', 'GitHub', 'Jira'].map(s => `<div class="conn-item"><span class="status-badge pending">${s}</span><span class="text-muted">checking...</span></div>`).join('');
    try {
        const data = await (await fetch('/api/status')).json();
        c.innerHTML = Object.entries(data).map(([k, v]) => {
            const label = k.charAt(0).toUpperCase() + k.slice(1);
            const dot = v.status === 'connected' ? 'ok' : (v.configured ? 'warn' : 'pending');
            const txt = v.status === 'connected' ? 'Connected' + (v.repo ? ` (${v.repo})` : '') : (v.configured ? v.status : 'Not configured');
            return `<div class="conn-item"><span class="status-badge ${dot}">${label}</span><span class="text-muted">${txt}</span></div>`;
        }).join('');
    } catch (e) { showAlert('settingsAlert', e.message); }
}

/* ═══ WEBHOOK STATUS ═══ */
async function loadWebhookStatus() {
    try {
        const data = await (await fetch('/api/webhooks/status')).json();
        if (!data.success) return;
        const wh = data.webhooks;

        // GitHub webhook
        const ghDot = document.getElementById('webhookGithubDot');
        const ghDetail = document.getElementById('webhookGithubDetail');
        if (wh.github.status === 'active') {
            ghDot.className = 'status-badge ok';
            ghDot.textContent = 'Active';
            ghDetail.innerHTML = `<span class="webhook-active">Active</span> — Endpoint: <code>${wh.github.endpoint}</code>` +
                (wh.github.secret_configured ? ' <span class="webhook-secure">🔒 Secret configured</span>' : ' <span class="webhook-warn">⚠ No secret</span>');
        } else {
            ghDot.className = 'status-badge pending';
            ghDot.textContent = 'Pending';
            ghDetail.textContent = 'Not configured — GitHub token required';
        }

        // Jira webhook
        const jiraDot = document.getElementById('webhookJiraDot');
        const jiraDetail = document.getElementById('webhookJiraDetail');
        if (wh.jira.status === 'active') {
            jiraDot.className = 'status-badge ok';
            jiraDot.textContent = 'Active';
            jiraDetail.innerHTML = `<span class="webhook-active">Active</span> — Endpoint: <code>${wh.jira.endpoint}</code>`;
        } else {
            jiraDot.className = 'status-badge pending';
            jiraDot.textContent = 'Pending';
            jiraDetail.textContent = 'Not configured — Jira URL and token required';
        }
    } catch (e) { }
}

document.getElementById('s_temperature')?.addEventListener('input', e => { document.getElementById('tempValue').textContent = e.target.value; });

/* ═══ PROMPTS ═══ */
async function loadPrompts() {
    try { promptsCache = await (await fetch('/api/prompts')).json(); document.getElementById('promptEditor').value = promptsCache[currentPromptMode] || ''; } catch (e) { }
}
function switchPromptTab(mode) {
    currentPromptMode = mode;
    document.querySelectorAll('.prompt-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.prompt-tab[data-prompt="${mode}"]`).classList.add('active');
    document.getElementById('promptEditor').value = promptsCache[mode] || '';
}
async function saveCurrentPrompt() {
    try {
        const data = await (await fetch('/api/prompts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: currentPromptMode, content: document.getElementById('promptEditor').value }) })).json();
        if (data.success) { promptsCache[currentPromptMode] = document.getElementById('promptEditor').value; showAlert('promptAlert', `'${currentPromptMode}' saved.`, 'success'); } else showAlert('promptAlert', data.message);
    } catch (e) { showAlert('promptAlert', e.message); }
}
