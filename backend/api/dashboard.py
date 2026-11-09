"""Embedded interactive dashboard for TickerWatch MVP."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TickerWatch — Local-First Financial Intelligence MVP</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --card-hover: #1c2128;
      --border: #30363d;
      --text-main: #e6edf3;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --accent-hover: #79c0ff;
      --success: #3fb950;
      --warning: #d29922;
      --danger: #f85149;
      --badge-bg: rgba(56, 139, 253, 0.15);
      --badge-text: #58a6ff;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text-main);
      line-height: 1.5;
      padding: 0;
      margin: 0;
    }

    /* Header */
    header {
      background-color: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 1rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .brand h1 {
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: -0.5px;
      color: #fff;
    }

    .brand span.tag {
      background: rgba(63, 185, 80, 0.15);
      color: var(--success);
      font-size: 0.75rem;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 12px;
      border: 1px solid rgba(63, 185, 80, 0.3);
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }

    .pulse {
      width: 6px;
      height: 6px;
      background: var(--success);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--success);
      display: inline-block;
    }

    .header-actions {
      display: flex;
      gap: 0.75rem;
      align-items: center;
    }

    .btn {
      background: #21262d;
      color: var(--text-main);
      border: 1px solid var(--border);
      padding: 0.45rem 0.9rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }

    .btn:hover {
      background: #30363d;
      border-color: #8b949e;
      color: #fff;
    }

    .btn-primary {
      background: #238636;
      border-color: rgba(240, 246, 252, 0.1);
      color: #fff;
    }

    .btn-primary:hover {
      background: #2ea043;
    }

    /* Container */
    .container {
      max-width: 1400px;
      margin: 1.5rem auto;
      padding: 0 1.5rem;
    }

    /* KPI Cards */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }

    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }

    .kpi-label {
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
    }

    .kpi-value {
      font-size: 1.85rem;
      font-weight: 700;
      color: #fff;
    }

    .kpi-sub {
      font-size: 0.75rem;
      color: var(--text-muted);
    }

    /* Section Headers */
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }

    .section-title {
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text-main);
    }

    /* Connectors Grid */
    .connectors-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }

    .connector-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
      transition: border-color 0.15s ease;
    }

    .connector-card:hover {
      border-color: #484f58;
    }

    .conn-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .conn-title {
      font-weight: 600;
      font-size: 0.95rem;
      color: #fff;
    }

    .badge {
      font-size: 0.72rem;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 10px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .badge-healthy {
      background: rgba(63, 185, 80, 0.15);
      color: var(--success);
      border: 1px solid rgba(63, 185, 80, 0.3);
    }

    .badge-degraded {
      background: rgba(210, 153, 34, 0.15);
      color: var(--warning);
      border: 1px solid rgba(210, 153, 34, 0.3);
    }

    .badge-failed {
      background: rgba(248, 81, 73, 0.15);
      color: var(--danger);
      border: 1px solid rgba(248, 81, 73, 0.3);
    }

    .conn-details {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.4rem;
      font-size: 0.8rem;
      color: var(--text-muted);
    }

    .conn-actions {
      margin-top: 0.25rem;
      display: flex;
      justify-content: flex-end;
    }

    /* Filters Bar */
    .filter-bar {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      display: flex;
      gap: 1rem;
      align-items: center;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }

    .search-box {
      flex: 1;
      min-width: 220px;
      position: relative;
    }

    .search-box input {
      width: 100%;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.45rem 0.75rem;
      color: var(--text-main);
      font-size: 0.85rem;
    }

    .search-box input:focus {
      outline: none;
      border-color: var(--accent);
    }

    .select-filter {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.45rem 0.75rem;
      color: var(--text-main);
      font-size: 0.85rem;
      cursor: pointer;
    }

    /* Articles Feed */
    .articles-feed {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }

    .article-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      transition: background-color 0.15s ease, border-color 0.15s ease;
    }

    .article-card:hover {
      background: var(--card-hover);
      border-color: #484f58;
    }

    .article-meta {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 0.75rem;
      color: var(--text-muted);
      flex-wrap: wrap;
    }

    .article-meta span.publisher {
      font-weight: 600;
      color: var(--accent);
    }

    .ticker-tag {
      background: rgba(88, 166, 255, 0.15);
      color: var(--accent);
      border: 1px solid rgba(88, 166, 255, 0.3);
      padding: 1px 6px;
      border-radius: 4px;
      font-weight: 700;
      font-size: 0.7rem;
      cursor: pointer;
    }

    .ticker-tag:hover {
      background: rgba(88, 166, 255, 0.3);
    }

    .article-title {
      font-size: 1.05rem;
      font-weight: 600;
      color: #fff;
      text-decoration: none;
      display: inline-block;
    }

    .article-title:hover {
      color: var(--accent-hover);
      text-decoration: underline;
    }

    .article-snippet {
      font-size: 0.85rem;
      color: #c9d1d9;
      line-height: 1.45;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: #238636;
      color: #fff;
      padding: 0.75rem 1.25rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 500;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      display: none;
      z-index: 1000;
    }

    .empty-state {
      background: var(--card-bg);
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 3rem;
      text-align: center;
      color: var(--text-muted);
    }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#58a6ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
      </svg>
      <h1>TickerWatch</h1>
      <span class="tag"><span class="pulse"></span> LIVE INGESTION ENGINE</span>
    </div>
    <div class="header-actions">
      <a href="/docs" target="_blank" class="btn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
        Swagger OpenAPI Docs
      </a>
      <a href="/health" target="_blank" class="btn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"></path></svg>
        API Health
      </a>
      <button class="btn btn-primary" onclick="loadAllData()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
        Refresh Dashboard
      </button>
    </div>
  </header>

  <div class="container">
    <!-- Top KPIs -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">Total Normalised Articles</span>
        <span class="kpi-value" id="kpi-articles">—</span>
        <span class="kpi-sub">Indexed in local SQLite + FTS5</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Active Connectors</span>
        <span class="kpi-value" id="kpi-connectors">—</span>
        <span class="kpi-sub" id="kpi-connectors-sub">Continuous background polling</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Ingestion Attempts</span>
        <span class="kpi-value" id="kpi-attempts">—</span>
        <span class="kpi-sub" id="kpi-success-rate">Success Rate: 100%</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Dead-Letter Queue</span>
        <span class="kpi-value" id="kpi-deadletter" style="color: var(--success);">0</span>
        <span class="kpi-sub">Unrecoverable payloads isolated</span>
      </div>
    </div>

    <!-- Connectors Monitor -->
    <div class="section-header">
      <h2 class="section-title">Data Ingestion Pipelines</h2>
      <span style="font-size: 0.8rem; color: var(--text-muted);">Paced under SEC & RSS exchange limits</span>
    </div>
    <div class="connectors-grid" id="connectors-grid">
      <!-- Injected via JS -->
    </div>

    <!-- Ingested Feed Section -->
    <div class="section-header">
      <h2 class="section-title">Ingested Financial Intelligence Feed</h2>
      <span style="font-size: 0.8rem; color: var(--text-muted);" id="feed-count">Loading articles...</span>
    </div>

    <div class="filter-bar">
      <div class="search-box">
        <input type="text" id="filter-ticker" placeholder="Filter by Ticker (e.g. AAPL, AMZN, MSFT)..." oninput="debounceFilter()">
      </div>
      <select id="filter-connector" class="select-filter" onchange="loadArticles()">
        <option value="">All Connectors</option>
        <option value="sec_edgar">SEC EDGAR Submissions (10-K, 8-K, 10-Q)</option>
        <option value="sec_news_press">SEC Press Releases</option>
        <option value="moneycontrol_market_reports">Moneycontrol India Reports</option>
      </select>
    </div>

    <div class="articles-feed" id="articles-feed">
      <!-- Injected via JS -->
    </div>
  </div>

  <div id="toast" class="toast">Operation completed successfully</div>

  <script>
    let debounceTimer;

    function showToast(message, isError = false) {
      const toast = document.getElementById('toast');
      toast.innerText = message;
      toast.style.background = isError ? '#f85149' : '#238636';
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 3000);
    }

    function timeAgo(dateString) {
      if (!dateString) return 'Never';
      const date = new Date(dateString);
      const now = new Date();
      const seconds = Math.floor((now - date) / 1000);
      if (seconds < 60) return `${seconds}s ago`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return `${hours}h ago`;
      const days = Math.floor(hours / 24);
      return `${days}d ago`;
    }

    async function loadStats() {
      try {
        const res = await fetch('/stats/ingestion');
        const data = await res.json();
        document.getElementById('kpi-articles').innerText = (data.total_articles || 0).toLocaleString();
        document.getElementById('kpi-attempts').innerText = (data.total_attempts || 0).toLocaleString();
        document.getElementById('kpi-deadletter').innerText = (data.dead_letter_count || 0).toLocaleString();
        if (data.dead_letter_count > 0) {
          document.getElementById('kpi-deadletter').style.color = 'var(--danger)';
        }

        const successRate = data.total_attempts > 0 
          ? Math.round(((data.total_attempts - (data.total_failed || 0)) / data.total_attempts) * 100)
          : 100;
        document.getElementById('kpi-success-rate').innerText = `Success Rate: ${successRate}% (${data.total_inserted || 0} inserted)`;
      } catch (e) {
        console.error('Error loading stats:', e);
      }
    }

    async function loadConnectors() {
      try {
        const res = await fetch('/connectors');
        const data = await res.json();
        const connectors = data.connectors || [];
        document.getElementById('kpi-connectors').innerText = connectors.filter(c => c.enabled).length;
        document.getElementById('kpi-connectors-sub').innerText = `${connectors.length} registered in registry`;

        const grid = document.getElementById('connectors-grid');
        grid.innerHTML = connectors.map(c => {
          const badgeClass = c.status === 'HEALTHY' ? 'badge-healthy' : (c.status === 'IDLE' ? 'badge-degraded' : 'badge-failed');
          return `
            <div class="connector-card">
              <div class="conn-header">
                <span class="conn-title">${c.name}</span>
                <span class="badge ${badgeClass}">${c.status}</span>
              </div>
              <div class="conn-details">
                <div><strong>ID:</strong> ${c.connector_id}</div>
                <div><strong>Interval:</strong> ${c.poll_interval_seconds}s</div>
                <div><strong>Last Fetch:</strong> ${timeAgo(c.last_successful_fetch)}</div>
                <div><strong>Cursor:</strong> <code style="font-size: 0.72rem;">${c.cursor ? c.cursor.substring(0, 15) + '...' : 'none'}</code></div>
              </div>
              <div class="conn-actions">
                <button class="btn" style="padding: 0.25rem 0.6rem; font-size: 0.78rem;" onclick="triggerConnector('${c.connector_id}')">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                  Trigger Poll
                </button>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        console.error('Error loading connectors:', e);
      }
    }

    async function triggerConnector(id) {
      showToast(`Triggering poll for ${id}...`);
      try {
        const res = await fetch(`/connectors/${id}/trigger`, { method: 'POST' });
        const result = await res.json();
        if (result.status === 'SUCCESS') {
          showToast(`Ingested ${result.inserted} new items from ${id}!`);
          loadAllData();
        } else {
          showToast(`Ingestion error: ${result.error || 'Failed'}`, true);
        }
      } catch (e) {
        showToast(`Trigger failed: ${e.message}`, true);
      }
    }

    async function loadArticles() {
      const ticker = document.getElementById('filter-ticker').value.trim();
      const connector = document.getElementById('filter-connector').value;

      let url = '/articles?limit=50';
      if (ticker) url += `&ticker=${encodeURIComponent(ticker.toUpperCase())}`;
      if (connector) url += `&connector_id=${encodeURIComponent(connector)}`;

      try {
        const res = await fetch(url);
        const articles = await res.json();
        document.getElementById('feed-count').innerText = `Showing latest ${articles.length} items`;

        const feed = document.getElementById('articles-feed');
        if (articles.length === 0) {
          feed.innerHTML = `
            <div class="empty-state">
              <h3>No articles found</h3>
              <p>Try clearing filters or click "Trigger Poll" on one of the connectors above.</p>
            </div>
          `;
          return;
        }

        feed.innerHTML = articles.map(a => {
          const tickersHtml = (a.tickers || []).map(t => 
            `<span class="ticker-tag" onclick="filterByTicker('${t}')">${t}</span>`
          ).join(' ');

          return `
            <div class="article-card">
              <div class="article-meta">
                <span class="publisher">${a.publisher || 'Unknown'}</span>
                <span>•</span>
                <span>${a.source_type.toUpperCase()}</span>
                <span>•</span>
                <span>${timeAgo(a.published_at_utc)}</span>
                ${tickersHtml ? '<span>•</span> ' + tickersHtml : ''}
              </div>
              <a href="${a.url}" target="_blank" class="article-title">${a.title}</a>
              <div class="article-snippet">${a.snippet || 'No text snippet available.'}</div>
            </div>
          `;
        }).join('');
      } catch (e) {
        console.error('Error loading articles:', e);
      }
    }

    function filterByTicker(ticker) {
      document.getElementById('filter-ticker').value = ticker;
      loadArticles();
    }

    function debounceFilter() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadArticles, 300);
    }

    function loadAllData() {
      loadStats();
      loadConnectors();
      loadArticles();
    }

    // Initial load
    loadAllData();
    // Poll updates every 15s
    setInterval(loadAllData, 15000);
  </script>
</body>
</html>
"""
