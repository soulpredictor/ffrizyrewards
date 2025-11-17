document.addEventListener("DOMContentLoaded", () => {
    const API_URL = "/api/leaderboard";
    const MAX_PLAYERS = 10;

    const formatCurrency = (value) => {
        const amount = Number(value) || 0;
        return amount.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    };

    const maskUsername = (username = "") => {
        if (username.length <= 4) {
            return username;
        }
        return `${username.slice(0, 4)}${"*".repeat(username.length - 4)}`;
    };

    fetch(API_URL, { cache: "no-store" })
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Shuffle API responded with ${response.status}`);
            }
            return response.json();
        })
        .then((data) => {
            if (!Array.isArray(data)) {
                throw new Error("Unexpected API response shape");
            }

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

                nameEl.textContent = maskUsername(player.username || "User");
                wagerEl.textContent = formatCurrency(player.wagerAmount);
            });
        })
        .catch((error) => {
            console.error("Failed to load leaderboard data:", error);
        });
});