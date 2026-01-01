// Helper function to create a Date object from Eastern time components
function createEasternDate(year, month, day, hour, minute, second) {
  // Create a date string in ISO format
  const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`;
  
  // Use Intl API to parse Eastern time
  // Create a date assuming it's UTC, then adjust for Eastern timezone
  const utcDate = new Date(dateStr + 'Z'); // Parse as UTC
  
  // Get the timezone offset for America/New_York at this date
  // We'll use a workaround: create the date and check offset
  const formatter = new Intl.DateTimeFormat('en', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
  
  // Get what the Eastern time would be for our UTC date
  const parts = formatter.formatToParts(utcDate);
  const easternYear = parseInt(parts.find(p => p.type === 'year').value);
  const easternMonth = parseInt(parts.find(p => p.type === 'month').value) - 1;
  const easternDay = parseInt(parts.find(p => p.type === 'day').value);
  const easternHour = parseInt(parts.find(p => p.type === 'hour').value);
  
  // Calculate offset: what UTC time gives us our desired Eastern time
  // If we want 23:59:59 Eastern, we need to find the UTC time that displays as 23:59:59 Eastern
  let testUTC = new Date(Date.UTC(year, month, day, hour, minute, second));
  
  // Adjust for timezone offset
  // Get what Eastern time this UTC time represents
  const testEastern = formatter.formatToParts(testUTC);
  const testEasternHour = parseInt(testEastern.find(p => p.type === 'hour').value);
  
  // Calculate offset: if testUTC shows as testEasternHour in Eastern, adjust
  // We want hour in Eastern, so adjust UTC accordingly
  const offsetHours = hour - testEasternHour;
  testUTC = new Date(testUTC.getTime() + (offsetHours * 60 * 60 * 1000));
  
  // More direct approach: use the fact that Eastern is UTC-5 (EST) or UTC-4 (EDT)
  // Check if DST is likely in effect (March to November, roughly)
  const isDST = month >= 2 && month <= 10;
  const offsetHours2 = isDST ? 4 : 5; // Eastern is UTC-4 (EDT) or UTC-5 (EST)
  
  // If we want 23:59:59 Eastern, that's 23:59:59 + offsetHours in UTC
  const targetUTC = new Date(Date.UTC(year, month, day, hour + offsetHours2, minute, second));
  
  // Verify by checking what Eastern time this UTC time represents
  const verifyEastern = formatter.formatToParts(targetUTC);
  const verifyHour = parseInt(verifyEastern.find(p => p.type === 'hour').value);
  
  // If not correct, adjust
  if (verifyHour !== hour) {
    const diff = hour - verifyHour;
    return new Date(targetUTC.getTime() + (diff * 60 * 60 * 1000));
  }
  
  return targetUTC;
}

// Calculate month end in Eastern Time (America/New_York)
function getMonthEndEastern() {
  const now = new Date();
  
  // Get current date in Eastern timezone
  const formatter = new Intl.DateTimeFormat('en', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric'
  });
  
  const parts = formatter.formatToParts(now);
  const year = parseInt(parts.find(p => p.type === 'year').value);
  const month = parseInt(parts.find(p => p.type === 'month').value) - 1; // 0-indexed
  
  // Get last day of current month
  const lastDay = new Date(year, month + 1, 0).getDate();
  
  // Create date for last day at 23:59:59 Eastern
  return createEasternDate(year, month, lastDay, 23, 59, 59);
}

const targetDate = getMonthEndEastern();

function calculateTimeRemaining() {
  const now = new Date();
  const difference = targetDate.getTime() - now.getTime();

  if (difference <= 0) {
    clearInterval(timerInterval);
    document.getElementById('countdown').innerHTML = '00D 00H 00M 00S';
    return;
  }

  const days = Math.floor(difference / (1000 * 60 * 60 * 24));
  const hours = Math.floor((difference % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
  const minutes = Math.floor((difference % (1000 * 60 * 60)) / (1000 * 60));
  const seconds = Math.floor((difference % (1000 * 60)) / 1000);

  document.getElementById('countdown').innerHTML = `${String(days).padStart(2, '0')}D ${String(hours).padStart(2, '0')}H ${String(minutes).padStart(2, '0')}M ${String(seconds).padStart(2, '0')}S`;
}

// Calculate initial time remaining
calculateTimeRemaining();

// Update the countdown every second
const timerInterval = setInterval(calculateTimeRemaining, 1000);
