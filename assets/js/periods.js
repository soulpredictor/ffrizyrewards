/**
 * Eastern-time period helpers for Shuffle (monthly) and Winovo (weekly).
 * Winovo week: Monday 00:00 ET → Sunday 00:00 ET (exclusive end).
 */
(function (global) {
    const ET = "America/New_York";

    function easternParts(date) {
        const formatter = new Intl.DateTimeFormat("en-US", {
            timeZone: ET,
            year: "numeric",
            month: "numeric",
            day: "numeric",
            weekday: "short",
            hour: "numeric",
            minute: "numeric",
            second: "numeric",
            hour12: false,
        });
        const parts = formatter.formatToParts(date);
        const get = (type) => parts.find((p) => p.type === type)?.value;
        return {
            year: parseInt(get("year"), 10),
            month: parseInt(get("month"), 10) - 1,
            day: parseInt(get("day"), 10),
            hour: parseInt(get("hour"), 10),
            minute: parseInt(get("minute"), 10),
            second: parseInt(get("second"), 10),
        };
    }

    /** UTC instant when local ET wall-clock is y-m-d H:M:S */
    function easternToUtc(year, month, day, hour, minute, second) {
        let guess = Date.UTC(year, month, day, hour + 5, minute, second);
        for (let i = 0; i < 3; i++) {
            const p = easternParts(new Date(guess));
            const diffHours = hour - p.hour;
            const diffDays = day - p.day;
            const adjustMs = (diffDays * 24 + diffHours) * 3600000;
            if (adjustMs === 0) break;
            guess += adjustMs;
        }
        return guess;
    }

    function getMonthBoundsEastern(now = new Date()) {
        const p = easternParts(now);
        const lastDay = new Date(p.year, p.month + 1, 0).getDate();
        const start = easternToUtc(p.year, p.month, 1, 0, 0, 0);
        const end = easternToUtc(p.year, p.month, lastDay, 23, 59, 59);
        return { start, end, label: "monthly" };
    }

    function getWeekBoundsEastern(now = new Date()) {
        const p = easternParts(now);
        const d = new Date(easternToUtc(p.year, p.month, p.day, 12, 0, 0));
        const dp = easternParts(d);
        const noonUtc = easternToUtc(dp.year, dp.month, dp.day, 12, 0, 0);
        const weekday = new Date(noonUtc).getUTCDay();
        const daysFromMonday = (weekday + 6) % 7;
        const mondayUtc = noonUtc - daysFromMonday * 86400000;
        const mp = easternParts(new Date(mondayUtc));
        const start = easternToUtc(mp.year, mp.month, mp.day, 0, 0, 0);
        const end = start + 6 * 86400000;
        return { start, end, label: "weekly" };
    }

    function getPeriodBounds(site, now = new Date()) {
        return site === "winovo" ? getWeekBoundsEastern(now) : getMonthBoundsEastern(now);
    }

    function formatEasternRange(startMs, endMs) {
        const opts = { timeZone: ET, month: "short", day: "numeric", year: "numeric" };
        const startStr = new Date(startMs).toLocaleString("en-US", opts);
        const endDisplay = new Date(endMs - 1);
        const endStr = endDisplay.toLocaleString("en-US", opts);
        return `${startStr} – ${endStr}`;
    }

    global.LeaderboardPeriods = {
        ET,
        getMonthBoundsEastern,
        getWeekBoundsEastern,
        getPeriodBounds,
        formatEasternRange,
        easternToUtc,
    };
})(typeof window !== "undefined" ? window : globalThis);
