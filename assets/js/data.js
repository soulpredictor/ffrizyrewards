document.addEventListener("DOMContentLoaded", () => {
    const backendBase = (window.__BACKEND_BASE__ || "").toString().replace(/\/+$/, "");
    const apiOrigin = backendBase || window.location.origin;
    const ENDPOINTS = {
        shuffle: "/api/leaderboard",
        packy: "/api/leaderboard",
    };
    const MAX_PLAYERS = 10;
    const PRIZES = {
        shuffle: [1500, 750, 375, 225, 150, 20, 20, 20, 20, 20],
        packy: [300, 150, 75, 45, 30, 0, 0, 0, 0, 0],
    };

    let refreshInterval = null;
    let rolloverTimeout = null;
    let leaderboardEnded = false;
    let currentSite = localStorage.getItem("leaderboardSite") || "shuffle";
    const P = window.LeaderboardPeriods;
    const CACHE_PREFIX = "leaderboardCache:";
    const inFlight = { shuffle: null, packy: null };
    const requestSeq = { shuffle: 0, packy: 0 };

    const urlParams = new URLSearchParams(window.location.search);
    const dayParam = urlParams.get("day");

    let shuffleBounds = P.getPeriodBounds("shuffle");
    let packyBounds = P.getPeriodBounds("packy");
    let startTime = shuffleBounds.start;
    let endTime = shuffleBounds.end;

    if (currentSite === "winovo") {
        currentSite = "packy";
        localStorage.setItem("leaderboardSite", currentSite);
    }

    const cacheKey = (site) => `${CACHE_PREFIX}${site}`;

    const readCache = (site) => {
        try {
            const raw = localStorage.getItem(cacheKey(site));
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") return null;
            const players = Array.isArray(parsed.players) ? parsed.players : [];
            const etag = typeof parsed.etag === "string" ? parsed.etag : "";
            const dataHash = typeof parsed.dataHash === "string" ? parsed.dataHash : "";
            const period = parsed.period && typeof parsed.period === "object" ? parsed.period : null;
            const ended = Boolean(parsed.ended);
            return { players, etag, dataHash, period, ended };
        } catch {
            return null;
        }
    };

    const writeCache = (site, payload) => {
        try {
            localStorage.setItem(cacheKey(site), JSON.stringify(payload));
        } catch {}
    };

    if (dayParam && currentSite === "shuffle") {
        const day = parseInt(dayParam, 10);
        const now = new Date();
        const formatter = new Intl.DateTimeFormat("en", {
            timeZone: P.ET,
            year: "numeric",
            month: "numeric",
        });
        const parts = formatter.formatToParts(now);
        const year = parseInt(parts.find((p) => p.type === "year").value, 10);
        const month = parseInt(parts.find((p) => p.type === "month").value, 10) - 1;
        if (day >= 1 && day <= 31) {
            startTime = P.easternToUtc(year, month, day, 0, 0, 0);
            endTime = P.easternToUtc(year, month, day, 23, 59, 59);
        }
    }

    const formatCurrency = (value) => {
        const amount = Number(value) || 0;
        return amount.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    };

    const updatePrizes = () => {
        const prizes = PRIZES[currentSite] || PRIZES.shuffle;
        const prizeEls = document.querySelectorAll("[data-prize-rank]");
        prizeEls.forEach((el) => {
            const rank = parseInt(el.getAttribute("data-prize-rank"), 10) - 1;
            const amount = prizes[rank];
            if (amount > 0) {
                el.innerHTML = `<i class="fa-solid fa-dollar-sign"></i>${amount.toLocaleString("en-US")}`;
                el.closest("tr, .lead-card, .podium-card")?.classList.remove("prize-hidden");
            } else {
                el.textContent = "—";
                el.closest("tr")?.classList.add("prize-hidden");
            }
        });

        const total = prizes.slice(0, 5).reduce((a, b) => a + b, 0);
        const totalEl = document.getElementById("prizePoolTotal");
        if (totalEl) {
            totalEl.textContent = `$${total.toLocaleString("en-US")}`;
        }
    };

    const updateHeroCopy = () => {
        const titlePeriod = document.getElementById("leaderboardPeriodLabel");
        const descEl = document.getElementById("leaderboardDescription");
        const rangeEl = document.getElementById("leaderboardPeriodRange");

        const isWeekly = currentSite === "packy";
        if (titlePeriod) {
            titlePeriod.textContent = isWeekly ? "Weekly" : "Monthly";
        }
        if (descEl) {
            descEl.textContent = isWeekly
                ? "based on their total wagered amount for the current week (Mon–Sun ET)."
                : "based on their total wagered amount for the current month.";
        }
        if (rangeEl) {
            const bounds = isWeekly ? packyBounds : shuffleBounds;
            rangeEl.textContent = P.formatEasternRange(bounds.start, bounds.end);
        }
    };

    const updateSiteUI = () => {
        document.querySelectorAll("[data-leaderboard-site]").forEach((el) => {
            const site = el.getAttribute("data-leaderboard-site");
            const active = site === currentSite;
            el.classList.toggle("active", active);
            el.setAttribute("aria-selected", active ? "true" : "false");
        });

        const labelEl = document.getElementById("leaderboardSiteLabel");
        if (labelEl) {
            labelEl.textContent = currentSite === "packy" ? "Packy" : "Shuffle";
        }

        const playBtn = document.querySelector(".play-now-btn a, #navbarNav .btn-custom");
        if (playBtn) {
            playBtn.href =
                currentSite === "packy"
                    ? "https://packy.gg/"
                    : "https://shuffle.com/?r=ffrizy";
        }

        updatePrizes();
        updateHeroCopy();
        window.dispatchEvent(new CustomEvent("leaderboardSiteChanged", { detail: { site: currentSite } }));
    };

    const startRefresh = () => {
        if (refreshInterval) return;
        refreshInterval = setInterval(updateLeaderboard, 12000);
    };

    const stopRefresh = () => {
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = null;
        }
    };

    const renderPlayers = (players) => {
        const sorted = (players || [])
            .filter((p) => p && typeof p === "object")
            .map((p) => ({
                username: p.username || p.name || "User",
                wagerAmount: Number(p.wagerAmount ?? p.wagered) || 0,
            }))
            .sort((a, b) => b.wagerAmount - a.wagerAmount)
            .slice(0, MAX_PLAYERS);

        for (let index = 0; index < MAX_PLAYERS; index++) {
            const nameEl = document.getElementById(`user${index}_name`);
            const wagerEl = document.getElementById(`user${index}_wager`);
            if (!nameEl || !wagerEl) continue;

            if (index < sorted.length && sorted[index]) {
                nameEl.textContent = sorted[index].username || "User";
                wagerEl.textContent = formatCurrency(sorted[index].wagerAmount);
            } else {
                nameEl.textContent = "----";
                wagerEl.textContent = "----";
            }
        }
    };

    document.querySelectorAll("[data-leaderboard-site]").forEach((el) => {
        el.addEventListener("click", (e) => {
            e.preventDefault();
            const next = el.getAttribute("data-leaderboard-site");
            if (!next || next === currentSite) return;

            const prevSite = currentSite;
            currentSite = next;
            localStorage.setItem("leaderboardSite", currentSite);
            leaderboardEnded = false;
            if (rolloverTimeout) {
                clearTimeout(rolloverTimeout);
                rolloverTimeout = null;
            }
            if (inFlight[prevSite]) {
                inFlight[prevSite].abort();
                inFlight[prevSite] = null;
            }

            if (currentSite === "shuffle") {
                shuffleBounds = P.getPeriodBounds("shuffle");
                startTime = shuffleBounds.start;
                endTime = shuffleBounds.end;
            } else {
                packyBounds = P.getPeriodBounds("packy");
            }

            const cached = readCache(currentSite);
            if (cached?.players?.length) {
                if (currentSite === "shuffle" && cached.period?.startTime && cached.period?.endTime) {
                    shuffleBounds = {
                        start: cached.period.startTime,
                        end: cached.period.endTime,
                        label: "monthly",
                    };
                    startTime = shuffleBounds.start;
                    endTime = shuffleBounds.end;
                }
                if (currentSite === "packy" && cached.period?.startTime && cached.period?.endTime) {
                    packyBounds = {
                        start: cached.period.startTime,
                        end: cached.period.endTime,
                        label: "weekly",
                    };
                }
                leaderboardEnded = Boolean(cached.ended);
                renderPlayers(cached.players);
            } else {
                renderPlayers([]);
            }

            updateSiteUI();
            startRefresh();
            updateLeaderboard();
        });
    });

    const updateLeaderboard = async () => {
        const site = currentSite;
        const seq = (requestSeq[site] = (requestSeq[site] || 0) + 1);
        if (inFlight[site]) return;
        const controller = new AbortController();
        inFlight[site] = controller;

        const url = new URL(ENDPOINTS[site] || ENDPOINTS.shuffle, apiOrigin);

        if (site === "shuffle") {
            const start = startTime;
            const end = endTime;
            url.searchParams.set("startTime", start.toString());
            url.searchParams.set("endTime", end.toString());
        } else {
            url.searchParams.set("site", "packy");
        }
        url.searchParams.set("_t", Date.now().toString());

        try {
            const cached = readCache(site);
            const res = await fetch(url, { cache: "no-store", signal: controller.signal });
            if (res.status === 304) return;
            if (!res.ok) throw new Error(`${site} API responded with ${res.status}`);

            const response = await res.json();
            const dataHash = (response && (response.data_hash || response.dataHash)) || "";

            const nextKey = dataHash;
            const prevKey = cached?.dataHash || "";
            if (nextKey && prevKey && nextKey === prevKey) return;

            if (site !== currentSite) return;
            if (seq !== requestSeq[site]) return;

            if (site === "shuffle") {
                const data =
                    response && Array.isArray(response.data)
                        ? response.data
                        : Array.isArray(response)
                          ? response
                          : [];

                if (response?.period?.startTime && response?.period?.endTime) {
                    shuffleBounds = {
                        start: response.period.startTime,
                        end: response.period.endTime,
                        label: "monthly",
                    };
                    startTime = shuffleBounds.start;
                    endTime = shuffleBounds.end;
                    updateHeroCopy();
                }

                if (response?.ended) {
                    leaderboardEnded = true;
                    stopRefresh();
                    if (rolloverTimeout) clearTimeout(rolloverTimeout);
                    const nextStart = (response.period?.endTime || endTime) + 1000;
                    const delay = Math.max(1000, nextStart - Date.now());
                    rolloverTimeout = setTimeout(() => {
                        leaderboardEnded = false;
                        shuffleBounds = P.getPeriodBounds("shuffle");
                        startTime = shuffleBounds.start;
                        endTime = shuffleBounds.end;
                        updateHeroCopy();
                        startRefresh();
                        updateLeaderboard();
                    }, delay);
                }

                renderPlayers(data);
                writeCache(site, {
                    players: data,
                    dataHash,
                    period: response?.period || null,
                    ended: Boolean(response?.ended),
                });
            } else {
                const data = response && Array.isArray(response.data) ? response.data : [];
                if (response?.period?.startTime && response?.period?.endTime) {
                    packyBounds = {
                        start: response.period.startTime,
                        end: response.period.endTime,
                        label: "weekly",
                    };
                    updateHeroCopy();
                }
                renderPlayers(data);
                writeCache(site, {
                    players: data,
                    dataHash,
                    period: response?.period || null,
                    ended: Boolean(response?.ended),
                });
            }
        } catch (error) {
            if (error && (error.name === "AbortError" || error.code === 20)) return;
            console.error("Failed to load leaderboard data:", error);
            if (site === currentSite && !(readCache(site)?.players?.length || 0)) {
                renderPlayers([]);
            }
        } finally {
            if (inFlight[site] === controller) inFlight[site] = null;
        }
    };

    const initialCache = readCache(currentSite);
    if (initialCache?.players?.length) {
        if (currentSite === "shuffle" && initialCache.period?.startTime && initialCache.period?.endTime) {
            shuffleBounds = {
                start: initialCache.period.startTime,
                end: initialCache.period.endTime,
                label: "monthly",
            };
            startTime = shuffleBounds.start;
            endTime = shuffleBounds.end;
        }
        if (currentSite === "packy" && initialCache.period?.startTime && initialCache.period?.endTime) {
            packyBounds = {
                start: initialCache.period.startTime,
                end: initialCache.period.endTime,
                label: "weekly",
            };
        }
        leaderboardEnded = Boolean(initialCache.ended);
        renderPlayers(initialCache.players);
    }

    updateSiteUI();
    updateLeaderboard();
    if (!leaderboardEnded || currentSite !== "shuffle") {
        startRefresh();
    }
});
