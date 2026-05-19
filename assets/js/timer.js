(function () {
    const P = window.LeaderboardPeriods;
    if (!P) return;

    let targetDate = P.getPeriodBounds("shuffle").end;
    let timerInterval = null;

    function getActiveSite() {
        return localStorage.getItem("leaderboardSite") || "shuffle";
    }

    function resetTarget() {
        const site = getActiveSite();
        targetDate = P.getPeriodBounds(site).end;
    }

    function calculateTimeRemaining() {
        const el = document.getElementById("countdown");
        if (!el) return;

        const now = Date.now();
        const difference = targetDate - now;

        if (difference <= 0) {
            el.textContent = "00D 00H 00M 00S";
            return;
        }

        const days = Math.floor(difference / 86400000);
        const hours = Math.floor((difference % 86400000) / 3600000);
        const minutes = Math.floor((difference % 3600000) / 60000);
        const seconds = Math.floor((difference % 60000) / 1000);

        el.textContent = `${String(days).padStart(2, "0")}D ${String(hours).padStart(2, "0")}H ${String(minutes).padStart(2, "0")}M ${String(seconds).padStart(2, "0")}S`;
    }

    function startTimer() {
        if (timerInterval) clearInterval(timerInterval);
        resetTarget();
        calculateTimeRemaining();
        timerInterval = setInterval(calculateTimeRemaining, 1000);
    }

    window.addEventListener("leaderboardSiteChanged", startTimer);
    document.addEventListener("DOMContentLoaded", startTimer);
})();
