document.addEventListener("DOMContentLoaded", () => {
    const backendBase = (window.__BACKEND_BASE__ || "").toString().replace(/\/+$/, "");
    const apiOrigin = backendBase || window.location.origin;
    const API_ENDPOINT = "/api/leaderboard";
    const MAX_PLAYERS = 10;
    const PRIZES = [1500, 800, 500, 350, 250, 175, 150, 125, 100, 50];

    const P = window.LeaderboardPeriods;
    const CACHE_KEY = "leaderboardCache:packy";

    let refreshInterval = null;
    let packyBounds = P.getPeriodBounds("packy");
    let inFlight = null;
    let requestSeq = 0;

    // ── Cache helpers ──────────────────────────────────────────────────────────

    const readCache = () => {
        try {
            const raw = localStorage.getItem(CACHE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") return null;
            return {
                players:  Array.isArray(parsed.players) ? parsed.players : [],
                dataHash: typeof parsed.dataHash === "string" ? parsed.dataHash : "",
                period:   parsed.period && typeof parsed.period === "object" ? parsed.period : null,
                ended:    Boolean(parsed.ended),
            };
        } catch {
            return null;
        }
    };

    const writeCache = (payload) => {
        try { localStorage.setItem(CACHE_KEY, JSON.stringify(payload)); } catch {}
    };

    // ── Currency formatter ────────────────────────────────────────────────────

    const formatCurrency = (value) =>
        (Number(value) || 0).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });

    // ── Prize display ─────────────────────────────────────────────────────────

    const updatePrizes = () => {
        document.querySelectorAll("[data-prize-rank]").forEach((el) => {
            const rank = parseInt(el.getAttribute("data-prize-rank"), 10) - 1;
            const amount = PRIZES[rank];
            if (amount > 0) {
                el.innerHTML = `<i class="fa-solid fa-dollar-sign"></i>${amount.toLocaleString("en-US")}`;
                el.closest("tr, .lead-card, .podium-card")?.classList.remove("prize-hidden");
            } else {
                el.textContent = "—";
                el.closest("tr")?.classList.add("prize-hidden");
            }
        });

        const total = PRIZES.reduce((a, b) => a + b, 0);
        const totalEl = document.getElementById("prizePoolTotal");
        if (totalEl) totalEl.textContent = `$${total.toLocaleString("en-US")}`;

        const topNEl = document.getElementById("prizePoolTopN");
        if (topNEl) topNEl.textContent = `top ${PRIZES.filter((p) => p > 0).length} users`;
    };

    // ── Hero copy ─────────────────────────────────────────────────────────────

    const updateHeroCopy = () => {
        const titlePeriodEl = document.getElementById("leaderboardPeriodLabel");
        const descEl        = document.getElementById("leaderboardDescription");
        const rangeEl       = document.getElementById("leaderboardPeriodRange");

        if (titlePeriodEl) titlePeriodEl.textContent = "Monthly";
        if (descEl) {
            const rangeStr = P.formatEasternRange(packyBounds.start, packyBounds.end);
            descEl.textContent = `based on their total wagered amount for the ${rangeStr} ET period.`;
        }
        if (rangeEl) rangeEl.textContent = P.formatEasternRange(packyBounds.start, packyBounds.end);
    };

    // ── Site UI (always Stake) ────────────────────────────────────────────────

    const updateSiteUI = () => {
        const labelEl = document.getElementById("leaderboardSiteLabel");
        if (labelEl) labelEl.textContent = "Stake";

        const playBtn = document.querySelector(".play-now-btn a, #navbarNav .btn-custom");
        if (playBtn) playBtn.href = "https://stake.com/?c=ffrizy";

        updatePrizes();
        updateHeroCopy();
        window.dispatchEvent(new CustomEvent("leaderboardSiteChanged", { detail: { site: "packy" } }));
    };

    // ── Refresh ────────────────────────────────────────────────────────────────

    const startRefresh = () => {
        if (refreshInterval) return;
        refreshInterval = setInterval(updateLeaderboard, 12000);
    };

    // ── Render players ────────────────────────────────────────────────────────

    const renderPlayers = (players) => {
        const sorted = (players || [])
            .filter((p) => p && typeof p === "object")
            .map((p) => ({
                username: p.username || p.name || "User",
                wagerAmount: Number(p.wagerAmount ?? p.wagered) || 0,
            }))
            .sort((a, b) => b.wagerAmount - a.wagerAmount)
            .slice(0, MAX_PLAYERS);

        for (let i = 0; i < MAX_PLAYERS; i++) {
            const nameEl  = document.getElementById(`user${i}_name`);
            const wagerEl = document.getElementById(`user${i}_wager`);
            if (!nameEl || !wagerEl) continue;

            if (i < sorted.length && sorted[i]) {
                nameEl.textContent  = sorted[i].username || "User";
                wagerEl.textContent = formatCurrency(sorted[i].wagerAmount);
            } else {
                nameEl.textContent  = "----";
                wagerEl.textContent = "----";
            }
        }
    };

    // ── Fetch leaderboard ─────────────────────────────────────────────────────

    const updateLeaderboard = async () => {
        const seq = ++requestSeq;
        if (inFlight) return;

        const controller = new AbortController();
        inFlight = controller;

        const url = new URL(API_ENDPOINT, apiOrigin);
        url.searchParams.set("site", "packy");
        url.searchParams.set("_t", Date.now().toString());

        try {
            const cached = readCache();
            const res = await fetch(url, { cache: "no-store", signal: controller.signal });
            if (res.status === 304) return;
            if (!res.ok) throw new Error(`Stake API responded with ${res.status}`);

            const response = await res.json();
            const dataHash = (response && (response.data_hash || response.dataHash)) || "";

            // Skip re-render if data hasn't changed
            if (dataHash && cached?.dataHash && dataHash === cached.dataHash) return;
            if (seq !== requestSeq) return;

            const data = response && Array.isArray(response.data) ? response.data : [];

            if (response?.period?.startTime && response?.period?.endTime) {
                packyBounds = {
                    start: response.period.startTime,
                    end:   response.period.endTime,
                    label: "monthly",
                };
                updateHeroCopy();
            }

            renderPlayers(data);
            writeCache({
                players:  data,
                dataHash,
                period:   response?.period || null,
                ended:    Boolean(response?.ended),
            });
        } catch (error) {
            if (error && (error.name === "AbortError" || error.code === 20)) return;
            console.error("Failed to load Stake leaderboard:", error);
            if (!(readCache()?.players?.length)) renderPlayers([]);
        } finally {
            if (inFlight === controller) inFlight = null;
        }
    };

    // ── Init ───────────────────────────────────────────────────────────────────

    const initialCache = readCache();
    if (initialCache?.players?.length) {
        if (initialCache.period?.startTime && initialCache.period?.endTime) {
            packyBounds = {
                start: initialCache.period.startTime,
                end:   initialCache.period.endTime,
                label: "monthly",
            };
        }
        renderPlayers(initialCache.players);
    }

    updateSiteUI();
    updateLeaderboard();
    startRefresh();
});
