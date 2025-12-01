document.addEventListener("DOMContentLoaded", () => {
    const API_URL = "/api/leaderboard";
    const MAX_PLAYERS = 10;
    let refreshInterval = null;
    let leaderboardEnded = false;

    // Get URL parameters to check if viewing a specific day
    const urlParams = new URLSearchParams(window.location.search);
    const dayParam = urlParams.get('day'); // e.g., ?day=1 for December 1st

    // Default leaderboard period: NOW to December 30, 2025
    const now = new Date();
    const defaultStartTime = now.getTime(); // Start from now
    
    // End: December 30, 2025 at 23:59:59 UTC
    const endDate = new Date(Date.UTC(2025, 11, 30, 23, 59, 59)); // December 30, 2025
    const defaultEndTime = endDate.getTime();

    // If day parameter is provided (e.g., ?day=1), show data for that specific day
    let startTime, endTime;
    if (dayParam) {
        const day = parseInt(dayParam);
        if (day >= 1 && day <= 31) {
            // Show data for specific day: December [day], 2025
            const dayStart = new Date(Date.UTC(2025, 11, day, 0, 0, 0)); // December [day], 00:00:00
            const dayEnd = new Date(Date.UTC(2025, 11, day, 23, 59, 59)); // December [day], 23:59:59
            startTime = dayStart.getTime();
            endTime = dayEnd.getTime();
            console.log(`Showing wagers for December ${day}, 2025`);
        } else {
            // Invalid day, use default
            startTime = defaultStartTime;
            endTime = defaultEndTime;
        }
    } else {
        // Default: Show wagers from now to December 30
        startTime = defaultStartTime;
        endTime = defaultEndTime;
    }

    const formatCurrency = (value) => {
        const amount = Number(value) || 0;
        return amount.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    };

    const updateLeaderboard = () => {
        // Build URL with startTime and endTime parameters
        // This limits wagers to only count during the specified period
        const url = new URL(API_URL, window.location.origin);
        url.searchParams.set("startTime", startTime.toString());
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
                        // Stop auto-refresh after leaderboard ends, but still allow manual refresh
                        if (refreshInterval) {
                            clearInterval(refreshInterval);
                            refreshInterval = null;
                        }
                    }
                } else {
                    console.error("Unexpected API response shape:", response);
                    data = [];
                }

                // Sort and display data
                const sorted = data
                    .filter((player) => player && typeof player?.wagerAmount === "number")
                    .sort((a, b) => b.wagerAmount - a.wagerAmount)
                    .slice(0, MAX_PLAYERS);

                // Update all player slots (fill with empty if no data)
                for (let index = 0; index < MAX_PLAYERS; index++) {
                    const nameEl = document.getElementById(`user${index}_name`);
                    const wagerEl = document.getElementById(`user${index}_wager`);

                    if (!nameEl || !wagerEl) {
                        continue;
                    }

                    if (index < sorted.length && sorted[index]) {
                        const player = sorted[index];
                        // Username is already masked by the backend API
                        nameEl.textContent = player.username || "User";
                        wagerEl.textContent = formatCurrency(player.wagerAmount);
                    } else {
                        // Show placeholder if no data for this rank
                        nameEl.textContent = "----";
                        wagerEl.textContent = "----";
                    }
                }
            })
            .catch((error) => {
                console.error("Failed to load leaderboard data:", error);
            });
    };

    // Initial load
    updateLeaderboard();

    // Refresh every 20 seconds (matching backend polling interval)
    // Continue refreshing even after leaderboard ends to show final data
    refreshInterval = setInterval(() => {
        updateLeaderboard();
    }, 20000); // 20 seconds
});
