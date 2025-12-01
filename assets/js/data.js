document.addEventListener("DOMContentLoaded", () => {
    const API_URL = "/api/leaderboard";
    const MAX_PLAYERS = 10;
    let refreshInterval = null;
    let leaderboardEnded = false;

    // Get the end time from the timer (same as timer.js)
    const targetDate = new Date(Date.UTC(2025, 10, 30, 18 + 7, 59, 59)); // MST = UTC-7
    const endTime = targetDate.getTime(); // Milliseconds timestamp

    const formatCurrency = (value) => {
        const amount = Number(value) || 0;
        return amount.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    };

    const updateLeaderboard = () => {
        // Check if leaderboard has ended (but still show data, just stop refreshing)
        const now = new Date().getTime();
        if (now >= endTime) {
            leaderboardEnded = true;
            if (refreshInterval) {
                clearInterval(refreshInterval);
                refreshInterval = null;
            }
        }

        // Build URL with endTime parameter (always send it so backend uses it to limit wagers)
        const url = new URL(API_URL, window.location.origin);
        url.searchParams.set("endTime", endTime.toString());

        fetch(url, { cache: "no-store" })
            .then((response) => {
                if (!response.ok) {
                    throw new Error(`Shuffle API responded with ${response.status}`);
                }
                return response.json();
            })
            .then((response) => {
                // Handle both old format (array) and new format (object with data and ended)
                let data;
                if (Array.isArray(response)) {
                    data = response;
                } else if (response.data && Array.isArray(response.data)) {
                    data = response.data;
                    if (response.ended) {
                        leaderboardEnded = true;
                        if (refreshInterval) {
                            clearInterval(refreshInterval);
                            refreshInterval = null;
                        }
                    }
                } else {
                    throw new Error("Unexpected API response shape");
                }

                // Always display data, even if leaderboard has ended
                const sorted = data
                    .filter((player) => typeof player?.wagerAmount === "number")
                    .sort((a, b) => b.wagerAmount - a.wagerAmount)
                    .slice(0, MAX_PLAYERS);

                sorted.forEach((player, index) => {
                    const nameEl = document.getElementById(`user${index}_name`);
                    const wagerEl = document.getElementById(`user${index}_wager`);

                    if (!nameEl || !wagerEl) {
                        return;
                    }

                    // Username is already masked by the backend API
                    nameEl.textContent = player.username || "User";
                    wagerEl.textContent = formatCurrency(player.wagerAmount);
                });
            })
            .catch((error) => {
                console.error("Failed to load leaderboard data:", error);
            });
    };

    // Initial load
    updateLeaderboard();

    // Refresh every 20 seconds (matching backend polling interval) if leaderboard hasn't ended
    if (!leaderboardEnded) {
        refreshInterval = setInterval(() => {
            if (!leaderboardEnded) {
                updateLeaderboard();
            } else {
                clearInterval(refreshInterval);
                refreshInterval = null;
            }
        }, 20000); // 20 seconds
    }
});
