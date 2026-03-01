document.addEventListener("DOMContentLoaded", () => {
    const API_URL = "/api/leaderboard";
    const MAX_PLAYERS = 10;
    let refreshInterval = null;
    let leaderboardEnded = false;

    // Helper to create Date from Eastern time components
    function createEasternDate(year, month, day, hour, minute, second) {
        // Eastern is UTC-5 (EST) or UTC-4 (EDT)
        // Rough DST check: March (2) to November (10)
        const isDST = month >= 2 && month <= 10;
        const offsetHours = isDST ? 4 : 5;
        
        // If we want hour:minute:second Eastern, add offset to get UTC
        return new Date(Date.UTC(year, month, day, hour + offsetHours, minute, second));
    }
    
    // Calculate current month start and end in Eastern Time
    function getMonthStartEastern() {
        const now = new Date();
        const formatter = new Intl.DateTimeFormat('en', {
            timeZone: 'America/New_York',
            year: 'numeric',
            month: 'numeric'
        });
        const parts = formatter.formatToParts(now);
        const year = parseInt(parts.find(p => p.type === 'year').value);
        const month = parseInt(parts.find(p => p.type === 'month').value) - 1;
        
        // First day of month at 00:00:00 Eastern
        return createEasternDate(year, month, 1, 0, 0, 0);
    }
    
    function getMonthEndEastern() {
        const now = new Date();
        const formatter = new Intl.DateTimeFormat('en', {
            timeZone: 'America/New_York',
            year: 'numeric',
            month: 'numeric'
        });
        const parts = formatter.formatToParts(now);
        const year = parseInt(parts.find(p => p.type === 'year').value);
        const month = parseInt(parts.find(p => p.type === 'month').value) - 1;
        
        // Last day of current month at 23:59:59 Eastern
        const lastDay = new Date(year, month + 1, 0).getDate();
        return createEasternDate(year, month, lastDay, 23, 59, 59);
    }

    // Get URL parameters to check if viewing a specific day
    const urlParams = new URLSearchParams(window.location.search);
    const dayParam = urlParams.get('day');

    // Default: Current month in Eastern time (from 1st of month to last day of month)
    const monthStart = getMonthStartEastern();
    const monthEnd = getMonthEndEastern();
    const defaultStartTime = monthStart.getTime();
    const defaultEndTime = monthEnd.getTime();
    
    const startDateStr = monthStart.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });
    const endDateStr = monthEnd.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });
    console.log(`Using monthly leaderboard: ${startDateStr} to ${endDateStr} (Eastern Time)`);
    console.log(`Timestamps: startTime=${defaultStartTime}, endTime=${defaultEndTime}`);

    // If day parameter is provided, show data for that specific day
    let startTime, endTime;
    if (dayParam) {
        const day = parseInt(dayParam);
        const formatter = new Intl.DateTimeFormat('en', {
            timeZone: 'America/New_York',
            year: 'numeric',
            month: 'numeric'
        });
        const parts = formatter.formatToParts(new Date());
        const year = parseInt(parts.find(p => p.type === 'year').value);
        const month = parseInt(parts.find(p => p.type === 'month').value) - 1;
        
        if (day >= 1 && day <= 31) {
            // Show data for specific day in current month
            const dayStart = createEasternDate(year, month, day, 0, 0, 0);
            const dayEnd = createEasternDate(year, month, day, 23, 59, 59);
            startTime = dayStart.getTime();
            endTime = dayEnd.getTime();
            console.log(`Showing wagers for day ${day} of current month`);
        } else {
            startTime = defaultStartTime;
            endTime = defaultEndTime;
        }
    } else {
        // Default: Current month (from 1st to last day)
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
        // Build URL with startTime and endTime parameters in exact format
        // Format: startTime=1764591959590&endTime=1767139199000 (milliseconds timestamps)
        // Add timestamp to prevent caching and ensure fresh data
        const url = new URL(API_URL, window.location.origin);
        url.searchParams.set("startTime", startTime.toString());
        url.searchParams.set("endTime", endTime.toString());
        url.searchParams.set("_t", Date.now().toString()); // Cache buster - ensures fresh data
        
        console.log(`[${new Date().toLocaleTimeString()}] Fetching fresh leaderboard data`);
        console.log(`URL: ${url.toString()}`);
        const startDateDisplay = new Date(startTime).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });
        const endDateDisplay = new Date(endTime).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });
        console.log(`Timestamps: startTime=${startTime} (${startDateDisplay} ET), endTime=${endTime} (${endDateDisplay} ET)`);

        // Fetch with no cache for fresh data - always get latest
        fetch(url, { 
            cache: "no-store",
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        })
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

                console.log(`✅ API returned ${data.length} entries (fresh monthly data)`);
                
                // Sort and display data (include entries with 0 wagerAmount too)
                // Always show latest data - no caching
                const sorted = data
                    .filter((player) => player && typeof player?.wagerAmount === "number")
                    .sort((a, b) => b.wagerAmount - a.wagerAmount)
                    .slice(0, MAX_PLAYERS);
                
                console.log(`📊 Displaying ${sorted.length} players (sorted by wagerAmount, latest data only)`);
                
                // Log if no data
                if (sorted.length === 0) {
                    console.warn("⚠️ No leaderboard data available. Possible reasons:");
                    console.warn("1. No wagers in current month period");
                    console.warn("2. API rate limit (waiting 12+ seconds between requests)");
                    console.warn("3. No referees found");
                    console.warn("4. API temporarily unavailable");
                } else {
                    console.log(`✅ Successfully displaying ${sorted.length} players with latest wager data`);
                }

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

    // Initial load immediately
    updateLeaderboard();

    // Refresh every 12 seconds to avoid rate limiting
    // API allows 1 request every 10 seconds, so 12 seconds gives buffer
    // Always fetch fresh data with exact startTime/endTime - no caching
    refreshInterval = setInterval(() => {
        updateLeaderboard();
    }, 12000); // 12 seconds to avoid rate limit (API: 1 req per 10 sec)
});
