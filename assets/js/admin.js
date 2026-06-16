(function () {
    const TOKEN_KEY = "ffrizyAdminToken";
    const backendBase = (window.__BACKEND_BASE__ || "").toString().replace(/\/+$/, "");
    const apiOrigin = backendBase || window.location.origin;

    const loginSection = document.getElementById("loginSection");
    const dashboardSection = document.getElementById("dashboardSection");
    const loginForm = document.getElementById("loginForm");
    const loginError = document.getElementById("loginError");
    const logoutBtn = document.getElementById("logoutBtn");
    const filterForm = document.getElementById("filterForm");
    const filterPeriod = document.getElementById("filterPeriod");
    const resultsBody = document.getElementById("resultsBody");
    const resultsEmpty = document.getElementById("resultsEmpty");
    const resultCount = document.getElementById("resultCount");
    const lastFetched = document.getElementById("lastFetched");

    function getToken() {
        return sessionStorage.getItem(TOKEN_KEY);
    }

    function setToken(token) {
        if (token) sessionStorage.setItem(TOKEN_KEY, token);
        else sessionStorage.removeItem(TOKEN_KEY);
    }

    function authHeaders() {
        return {
            Authorization: `Bearer ${getToken()}`,
            "Content-Type": "application/json",
        };
    }

    function showDashboard() {
        loginSection.classList.add("d-none");
        dashboardSection.classList.remove("d-none");
        logoutBtn.classList.remove("d-none");
    }

    function showLogin() {
        loginSection.classList.remove("d-none");
        dashboardSection.classList.add("d-none");
        logoutBtn.classList.add("d-none");
    }

    function formatMoney(n) {
        return Number(n || 0).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function formatCaptured(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        return d.toLocaleString("en-US", { timeZone: "America/New_York" }) + " ET";
    }

    function toApiUrl(path) {
        return new URL(path, apiOrigin).toString();
    }

    async function api(path, options = {}) {
        const res = await fetch(toApiUrl(path), {
            ...options,
            headers: { ...authHeaders(), ...(options.headers || {}) },
        });
        if (res.status === 401) {
            setToken(null);
            showLogin();
            throw new Error("Session expired");
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `Request failed (${res.status})`);
        }
        return res.json();
    }

    async function loadPeriods() {
        const site = document.getElementById("filterSite").value;
        const q = site ? `?site=${encodeURIComponent(site)}` : "";
        const data = await api(`/api/admin/periods${q}`);
        const current = filterPeriod.value;
        filterPeriod.innerHTML = '<option value="">Any period</option>';
        (data.periods || []).forEach((p) => {
            const opt = document.createElement("option");
            opt.value = p;
            opt.textContent = p;
            filterPeriod.appendChild(opt);
        });
        if (current && [...filterPeriod.options].some((o) => o.value === current)) {
            filterPeriod.value = current;
        }
    }

    function renderResults(snapshots) {
        resultsBody.innerHTML = "";
        resultCount.textContent = snapshots.length ? `(${snapshots.length})` : "";

        if (!snapshots.length) {
            resultsEmpty.classList.remove("d-none");
            return;
        }
        resultsEmpty.classList.add("d-none");

        snapshots.forEach((snap) => {
            const players = (snap.players || [])
                .slice()
                .sort((a, b) => b.wagerAmount - a.wagerAmount)
                .slice(0, 5);
            const summary = players
                .map((p, i) => `${i + 1}. ${p.username} — $${formatMoney(p.wagerAmount)}`)
                .join("<br>");

            const tr = document.createElement("tr");
            tr.innerHTML = `
        <td>${formatCaptured(snap.capturedAt)}</td>
        <td><span class="badge bg-secondary text-capitalize">${snap.site || "—"}</span></td>
        <td><code class="text-white-50">${snap.periodKey || "—"}</code></td>
        <td>${summary || "—"}</td>
      `;

            tr.addEventListener("click", () => {
                const existing = tr.nextElementSibling;
                if (existing && existing.classList.contains("admin-detail-row")) {
                    existing.remove();
                    return;
                }
                document.querySelectorAll(".admin-detail-row").forEach((r) => r.remove());

                const detail = document.createElement("tr");
                detail.className = "admin-detail-row";
                const fullList = (snap.players || [])
                    .slice()
                    .sort((a, b) => b.wagerAmount - a.wagerAmount)
                    .map((p) => `<li>${p.username}: $${formatMoney(p.wagerAmount)}</li>`)
                    .join("");
                detail.innerHTML = `
          <td colspan="4">
            <p class="text-white-50 small mb-2">Period: ${snap.periodStart || ""} → ${snap.periodEnd || ""}</p>
            <ul class="admin-player-list">${fullList || "<li>No players</li>"}</ul>
          </td>
        `;
                tr.after(detail);
            });

            resultsBody.appendChild(tr);
        });
    }

    async function searchWagers() {
        const params = new URLSearchParams();
        const site = document.getElementById("filterSite").value;
        const user = document.getElementById("filterUser").value.trim();
        const from = document.getElementById("filterFrom").value;
        const to = document.getElementById("filterTo").value;
        const periodKey = filterPeriod.value;

        if (site) params.set("site", site);
        if (user) params.set("user", user);
        if (from) params.set("from", new Date(from).toISOString());
        if (to) params.set("to", new Date(to).toISOString());
        if (periodKey) params.set("periodKey", periodKey);

        const data = await api(`/api/admin/wagers?${params.toString()}`);
        renderResults(data.data || []);
        lastFetched.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.classList.add("d-none");
        const password = document.getElementById("adminPassword").value;
        try {
            const res = await fetch(toApiUrl("/api/admin/login"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password }),
            });
            const body = await res.json();
            if (!res.ok) throw new Error(body.error || "Login failed");
            setToken(body.token);
            showDashboard();
            await loadPeriods();
            await searchWagers();
        } catch (err) {
            loginError.textContent = err.message || "Login failed";
            loginError.classList.remove("d-none");
        }
    });

    filterForm.addEventListener("submit", (e) => {
        e.preventDefault();
        searchWagers().catch(console.error);
    });

    document.getElementById("filterSite").addEventListener("change", () => {
        loadPeriods().catch(console.error);
    });

    document.getElementById("clearFilters").addEventListener("click", () => {
        filterForm.reset();
        loadPeriods().then(searchWagers).catch(console.error);
    });

    logoutBtn.addEventListener("click", () => {
        setToken(null);
        showLogin();
    });

    if (getToken()) {
        showDashboard();
        loadPeriods().then(searchWagers).catch(() => {
            setToken(null);
            showLogin();
        });
    } else {
        showLogin();
    }
})();
