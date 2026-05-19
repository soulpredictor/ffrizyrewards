document.addEventListener("DOMContentLoaded", () => {
    const backendBase = (window.__BACKEND_BASE__ || "").toString().replace(/\/+$/, "");
    const apiOrigin = backendBase || window.location.origin;
    const ENDPOINTS = {
        shuffle: "/api/leaderboard",
        winovo: "/api/leaderboard",
    };
    const MAX_PLAYERS = 10;
    const PRIZES = {
        shuffle: [1500, 750, 375, 225, 150, 20, 20, 20, 20, 20],
        winovo: [376, 188, 93, 56, 37, 0, 0, 0, 0, 0],
    };

    let refreshInterval = null;
    let leaderboardEnded = false;
    let currentSite = localStorage.getItem("leaderboardSite") || "shuffle";
    const P = window.LeaderboardPeriods;

    const urlParams = new URLSearchParams(window.location.search);
    const dayParam = urlParams.get("day");

    let shuffleBounds = P.getPeriodBounds("shuffle");
    let winovoBounds = P.getPeriodBounds("winovo");
    let startTime = shuffleBounds.start;
    let endTime = shuffleBounds.end;

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

        const isWinovo = currentSite === "winovo";
        if (titlePeriod) {
            titlePeriod.textContent = isWinovo ? "Weekly" : "Monthly";
        }
        if (descEl) {
            descEl.textContent = isWinovo
                ? "based on their total wagered amount for the current week (Mon–Sat ET)."
                : "based on their total wagered amount for the current month.";
        }
        if (rangeEl) {
            const bounds = isWinovo ? winovoBounds : shuffleBounds;
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
            labelEl.textContent = currentSite === "winovo" ? "Winovo" : "Shuffle";
        }

        const playBtn = document.querySelector(".play-now-btn a, #navbarNav .btn-custom");
        if (playBtn) {
            playBtn.href =
                currentSite === "winovo"
                    ? "https://winovo.io/?ref=ffrizy"
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

    document.querySelectorAll("[data-leaderboard-site]").forEach((el) => {
        el.addEventListener("click", (e) => {
            e.preventDefault();
            const next = el.getAttribute("data-leaderboard-site");
            if (!next || next === currentSite) return;

            currentSite = next;
            localStorage.setItem("leaderboardSite", currentSite);
            leaderboardEnded = false;

            if (currentSite === "shuffle") {
                shuffleBounds = P.getPeriodBounds("shuffle");
                startTime = shuffleBounds.start;
                endTime = shuffleBounds.end;
            } else {
                winovoBounds = P.getPeriodBounds("winovo");
            }

            updateSiteUI();
            startRefresh();
            updateLeaderboard();
        });
    });

    const updateLeaderboard = () => {
        const url = new URL(ENDPOINTS[currentSite] || ENDPOINTS.shuffle, apiOrigin);

        if (currentSite === "shuffle") {
            url.searchParams.set("startTime", startTime.toString());
            url.searchParams.set("endTime", endTime.toString());
        } else {
            url.searchParams.set("site", "winovo");
        }
        url.searchParams.set("_t", Date.now().toString());

        fetch(url, {
            cache: "no-store",
            headers: {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                Pragma: "no-cache",
            },
        })
            .then((response) => {
                if (!response.ok) {
                    throw new Error(`${currentSite} API responded with ${response.status}`);
                }
                return response.json();
            })
            .then((response) => {
                let normalized = [];

                if (currentSite === "shuffle") {
                    let data;
                    if (Array.isArray(response)) {
                        data = response;
                    } else if (response.data && Array.isArray(response.data)) {
                        data = response.data;
                        if (response.ended) {
                            leaderboardEnded = true;
                            stopRefresh();
                        }
                    } else {
                        data = [];
                    }
                    normalized = data
                        .filter((player) => player && typeof player?.wagerAmount === "number")
                        .map((player) => ({
                            username: player.username || "User",
                            wagerAmount: Number(player.wagerAmount) || 0,
                        }));
                } else {
                    const data = response && Array.isArray(response.data) ? response.data : [];
                    normalized = data.map((entry) => ({
                        username: entry?.username || entry?.name || "User",
                        wagerAmount: Number(entry?.wagerAmount ?? entry?.wagered) || 0,
                    }));
                    if (response.period?.endTime) {
                        winovoBounds = {
                            start: response.period.startTime,
                            end: response.period.endTime,
                            label: "weekly",
                        };
                        updateHeroCopy();
                    }
                }

                const sorted = normalized
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
            })
            .catch((error) => {
                console.error("Failed to load leaderboard data:", error);
            });
    };

    updateSiteUI();
    updateLeaderboard();
    if (!leaderboardEnded || currentSite !== "shuffle") {
        startRefresh();
    }
});
