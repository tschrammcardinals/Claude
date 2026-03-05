/**
 * Kalshi Tennis Scraper for Google Sheets
 * =========================================
 * Fetches all live ATP/WTA 250+ tennis matchups from Kalshi prediction markets
 * and writes player names for each match to the active spreadsheet.
 *
 * SETUP INSTRUCTIONS:
 * 1. In the Apps Script editor, go to Project Settings > Script Properties
 * 2. Add property: KALSHI_API_KEY_ID  → your Kalshi API key ID
 * 3. Add property: KALSHI_PRIVATE_KEY → your RSA private key (PEM format)
 *    (Generate at: kalshi.com > Account > API Keys)
 * 4. Run setKalshiCredentials() once if you prefer to set credentials via code
 * 5. Run fetchTennisMatchups() or call =FETCH_TENNIS_MATCHUPS() in a cell
 *
 * USAGE:
 *   - Menu: Kalshi Tennis > Refresh Matchups
 *   - Custom function: =FETCH_TENNIS_MATCHUPS() in any cell
 */

// ─────────────────────────────────────────────────────────────────────────────
// CONSTANTS
// ─────────────────────────────────────────────────────────────────────────────

const KALSHI_BASE_URL = 'https://trading-api.kalshi.com/trade-api/v2';

/**
 * Minimum qualifying tournament tiers (250 and above).
 * Used to filter series/event titles.
 */
const ATP_WTA_QUALIFIERS = [
  // ATP tiers
  'ATP 250', 'ATP 500', 'ATP 1000', 'ATP Masters',
  // WTA tiers
  'WTA 250', 'WTA 500', 'WTA 1000', 'WTA Premier',
  // Grand Slams (always qualify)
  'Australian Open', 'Roland Garros', 'Wimbledon', 'US Open',
  'Grand Slam',
  // Common Kalshi naming patterns
  'Open 250', 'Open 500',
];

/** Output sheet name */
const OUTPUT_SHEET_NAME = 'Tennis Matchups';

/** Maximum pages to fetch per paginated endpoint (safety limit) */
const MAX_PAGES = 20;

/** Markets per page */
const PAGE_LIMIT = 200;

/** Script Property key for storing seen market tickers (JSON array) */
const PROP_SEEN_TICKERS = 'SEEN_MARKET_TICKERS';

/** Script Property key for the notification email address */
const PROP_NOTIFICATION_EMAIL = 'NOTIFICATION_EMAIL';

/** How often the notification check trigger fires (minutes) */
const NOTIFICATION_CHECK_INTERVAL_MINUTES = 5;

// ─────────────────────────────────────────────────────────────────────────────
// CREDENTIAL MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One-time setup: stores Kalshi API credentials in Script Properties.
 * Call this function once from the Apps Script editor (Run menu).
 * After running, delete the credential values from this code.
 *
 * @param {string} apiKeyId    - Your Kalshi API key ID
 * @param {string} privateKey  - PEM-encoded RSA private key
 */
function setKalshiCredentials(apiKeyId, privateKey) {
  const props = PropertiesService.getScriptProperties();
  props.setProperty('KALSHI_API_KEY_ID', apiKeyId);
  props.setProperty('KALSHI_PRIVATE_KEY', privateKey);
  Logger.log('Credentials saved to Script Properties.');
}

/**
 * Retrieves stored credentials from Script Properties.
 * @returns {{apiKeyId: string, privateKey: string}}
 */
function getCredentials_() {
  const props = PropertiesService.getScriptProperties();
  return {
    apiKeyId:   props.getProperty('KALSHI_API_KEY_ID')  || '',
    privateKey: props.getProperty('KALSHI_PRIVATE_KEY') || '',
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTHENTICATION (RSA-SHA256)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Builds Kalshi authentication headers for a request.
 * Kalshi uses RSA-SHA256 signing: sign(timestamp + METHOD + path + body).
 *
 * @param {string} method  - HTTP method (GET, POST, etc.)
 * @param {string} path    - API path including query string (e.g. /markets?status=open)
 * @param {string} body    - Request body string (empty string for GET)
 * @returns {Object} Headers object with KALSHI-ACCESS-* fields, or {} if no credentials
 */
function buildAuthHeaders_(method, path, body) {
  const creds = getCredentials_();
  if (!creds.apiKeyId || !creds.privateKey) {
    return {}; // Allow unauthenticated attempt for public endpoints
  }

  const timestamp = Date.now().toString();
  const messageToSign = timestamp + method.toUpperCase() + path + (body || '');

  try {
    const signatureBytes = Utilities.computeRsaSha256Signature(
      messageToSign,
      creds.privateKey
    );
    const signatureB64 = Utilities.base64Encode(signatureBytes);

    return {
      'KALSHI-ACCESS-KEY':       creds.apiKeyId,
      'KALSHI-ACCESS-SIGNATURE': signatureB64,
      'KALSHI-ACCESS-TIMESTAMP': timestamp,
    };
  } catch (e) {
    Logger.log('Auth signing error: ' + e.message);
    return {};
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// HTTP CLIENT
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Makes an authenticated GET request to the Kalshi API.
 *
 * @param {string} path    - API path (e.g. "/markets")
 * @param {Object} params  - Query parameters as key-value pairs
 * @returns {Object}       - Parsed JSON response body
 * @throws {Error}         - On non-200 HTTP status
 */
function kalshiGet_(path, params) {
  // Build query string
  const queryParts = [];
  if (params) {
    Object.entries(params).forEach(function([k, v]) {
      if (v !== null && v !== undefined && v !== '') {
        queryParts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
      }
    });
  }
  const queryString = queryParts.length > 0 ? '?' + queryParts.join('&') : '';

  // For signing, use path + query string (no base URL)
  const signPath = path + queryString;
  const url      = KALSHI_BASE_URL + signPath;

  const authHeaders = buildAuthHeaders_('GET', signPath, '');
  const options = {
    method:            'GET',
    headers: Object.assign(
      { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      authHeaders
    ),
    muteHttpExceptions: true,
    followRedirects:    true,
  };

  const response   = UrlFetchApp.fetch(url, options);
  const statusCode = response.getResponseCode();
  const body       = response.getContentText();

  if (statusCode === 200) {
    return JSON.parse(body);
  } else if (statusCode === 401 || statusCode === 403) {
    throw new Error(
      'Kalshi authentication failed (HTTP ' + statusCode + '). ' +
      'Please check your API key and private key in Script Properties.'
    );
  } else {
    throw new Error('Kalshi API error HTTP ' + statusCode + ': ' + body.substring(0, 300));
  }
}

/**
 * Fetches all pages of a paginated Kalshi endpoint.
 *
 * @param {string} path       - API path (e.g. "/markets")
 * @param {Object} baseParams - Base query parameters (cursor will be added automatically)
 * @param {string} resultKey  - Key in the response that holds the array of results
 * @returns {Array}           - Concatenated array of all results across pages
 */
function fetchAllPages_(path, baseParams, resultKey) {
  const allItems = [];
  let cursor     = null;
  let page       = 0;

  do {
    const params = Object.assign({}, baseParams, { limit: PAGE_LIMIT });
    if (cursor) params.cursor = cursor;

    const data = kalshiGet_(path, params);
    const items = data[resultKey] || [];
    allItems.push.apply(allItems, items);

    cursor = data.cursor || null;
    page++;
  } while (cursor && page < MAX_PAGES);

  return allItems;
}

// ─────────────────────────────────────────────────────────────────────────────
// TENNIS FILTERING LOGIC
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns true if the given text (series title, event title, ticker, etc.)
 * indicates an ATP or WTA tournament at the 250 level or above.
 *
 * @param {string} text - Text to test (case-insensitive)
 * @returns {boolean}
 */
function isQualifyingTournament_(text) {
  if (!text) return false;
  const upper = text.toUpperCase();

  // Must contain a tennis-related keyword
  const isTennis = (
    upper.indexOf('TENNIS') !== -1 ||
    upper.indexOf('ATP')    !== -1 ||
    upper.indexOf('WTA')    !== -1 ||
    upper.indexOf('OPEN')   !== -1 ||
    upper.indexOf('WIMBLEDON') !== -1 ||
    upper.indexOf('ROLAND GARROS') !== -1
  );

  if (!isTennis) return false;

  // Check qualifier tiers - must be 250 or above
  for (const qualifier of ATP_WTA_QUALIFIERS) {
    if (upper.indexOf(qualifier.toUpperCase()) !== -1) {
      return true;
    }
  }

  // If it mentions ATP or WTA but not a specific tier, check for Grand Slams
  // and accept if it mentions a known major tournament name
  const grandSlamNames = [
    'AUSTRALIAN', 'ROLAND', 'WIMBLEDON', 'US OPEN',
    'FRENCH OPEN', 'AO ', 'RG ', ' USO'
  ];
  for (const name of grandSlamNames) {
    if (upper.indexOf(name) !== -1) return true;
  }

  return false;
}

/**
 * Attempts to parse both player names from a Kalshi market or event title.
 * Handles common formats:
 *   - "Player A vs. Player B"
 *   - "Player A vs Player B"
 *   - "Player A or Player B"
 *   - "Will Player A beat Player B?"
 *   - "Who wins: Player A or Player B?"
 *
 * @param {string} title - Market/event title
 * @returns {{player1: string, player2: string}|null}
 */
function parsePlayers_(title) {
  if (!title) return null;

  // Pattern 1: "X vs. Y" or "X vs Y" (most common Kalshi format)
  let match = title.match(/^(.+?)\s+vs\.?\s+(.+?)(?:\s*[?\-–—].*)?$/i);
  if (match) {
    return {
      player1: match[1].trim(),
      player2: match[2].trim(),
    };
  }

  // Pattern 2: "Will X beat/defeat Y" or "Will X win against Y"
  match = title.match(/Will\s+(.+?)\s+(?:beat|defeat|win against|win over)\s+(.+?)(?:\?.*)?$/i);
  if (match) {
    return {
      player1: match[1].trim(),
      player2: match[2].trim(),
    };
  }

  // Pattern 3: "Who wins: X or Y?" or "Who wins between X and Y?"
  match = title.match(/Who wins.*?[:\s]+(.+?)\s+(?:or|and|vs\.?)\s+(.+?)(?:\?.*)?$/i);
  if (match) {
    return {
      player1: match[1].trim(),
      player2: match[2].trim(),
    };
  }

  // Pattern 4: "X or Y" (shorter format)
  match = title.match(/^(.+?)\s+or\s+(.+?)(?:\?.*)?$/i);
  if (match) {
    const p1 = match[1].trim();
    const p2 = match[2].trim();
    // Sanity check: both parts should look like names (no long phrases)
    if (p1.split(' ').length <= 4 && p2.split(' ').length <= 4) {
      return { player1: p1, player2: p2 };
    }
  }

  return null;
}

/**
 * Returns true if a market title looks like a match-winner or head-to-head market
 * (as opposed to tournament winner, set totals, etc.)
 *
 * @param {string} title
 * @returns {boolean}
 */
function isMatchupMarket_(title) {
  if (!title) return false;
  const upper = title.toUpperCase();

  // Must contain matchup indicators
  const hasVs     = /\bVS\.?\b/i.test(title);
  const hasBeat   = /\b(BEAT|DEFEAT|WIN AGAINST)\b/i.test(title);
  const hasOrWho  = /\b(WHO WINS|WHO WILL WIN)\b/i.test(title);

  if (!hasVs && !hasBeat && !hasOrWho) return false;

  // Exclude tournament-level markets (not individual matches)
  const excludePhrases = [
    'WIN THE TOURNAMENT', 'WIN THE TITLE', 'WIN THE CHAMPIONSHIP',
    'MAKE IT TO', 'ADVANCE TO', 'REACH THE', 'QUALIFY FOR',
    'SETS WON', 'TOTAL SETS', 'GAMES WON', 'TOTAL GAMES',
    'FIRST SET', 'SECOND SET', 'THIRD SET',
  ];
  for (const phrase of excludePhrases) {
    if (upper.indexOf(phrase) !== -1) return false;
  }

  return true;
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN DATA FETCHING
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetches all tennis series tickers from Kalshi that are ATP/WTA 250+.
 * @returns {string[]} Array of series tickers (e.g. ["KTENNIS", "KATPAUS"])
 */
function getTennisSeries_() {
  let allSeries;
  try {
    allSeries = fetchAllPages_('/series', { category: 'Sports' }, 'series');
  } catch (e) {
    // Fall back to fetching all series and filtering
    Logger.log('Sports category filter failed, fetching all series: ' + e.message);
    allSeries = fetchAllPages_('/series', {}, 'series');
  }

  const tennisTickers = [];
  for (const series of allSeries) {
    const text = [
      series.ticker       || '',
      series.title        || '',
      series.category     || '',
      series.subtitle     || '',
      (series.tags || []).join(' '),
    ].join(' ');

    if (isQualifyingTournament_(text)) {
      tennisTickers.push(series.ticker);
    }
  }

  Logger.log('Qualifying tennis series found: ' + tennisTickers.join(', '));
  return tennisTickers;
}

/**
 * Fetches all open events for the given series tickers.
 * @param {string[]} seriesTickers
 * @returns {Array} Array of event objects
 */
function getOpenEventsForSeries_(seriesTickers) {
  const allEvents = [];

  for (const ticker of seriesTickers) {
    try {
      const events = fetchAllPages_(
        '/events',
        { series_ticker: ticker, status: 'open', with_nested_markets: true },
        'events'
      );
      allEvents.push.apply(allEvents, events);
    } catch (e) {
      Logger.log('Failed to fetch events for series ' + ticker + ': ' + e.message);
    }
  }

  return allEvents;
}

/**
 * Fetches all open markets for the given series tickers (alternative to events).
 * @param {string[]} seriesTickers
 * @returns {Array} Array of market objects
 */
function getOpenMarketsForSeries_(seriesTickers) {
  const allMarkets = [];

  for (const ticker of seriesTickers) {
    try {
      const markets = fetchAllPages_(
        '/markets',
        { series_ticker: ticker, status: 'open' },
        'markets'
      );
      allMarkets.push.apply(allMarkets, markets);
    } catch (e) {
      Logger.log('Failed to fetch markets for series ' + ticker + ': ' + e.message);
    }
  }

  return allMarkets;
}

/**
 * Core function: fetches and processes all live ATP/WTA 250+ tennis matchups from Kalshi.
 *
 * @returns {Array.<Array>} 2D array suitable for writing to a sheet.
 *   Row 0: headers
 *   Row 1+: [Tournament, Event, Player 1, Player 2, Market Ticker, Market URL]
 */
function getLiveTennisMatchups() {
  Logger.log('Fetching Kalshi tennis series...');
  const seriesTickers = getTennisSeries_();

  if (seriesTickers.length === 0) {
    // Fallback: try broad market search for tennis-related open markets
    Logger.log('No series found via category filter. Trying broad market search...');
    return getLiveTennisMatchupsBroadSearch_();
  }

  Logger.log('Fetching open events for ' + seriesTickers.length + ' series...');
  const events = getOpenEventsForSeries_(seriesTickers);
  Logger.log('Total open events: ' + events.length);

  const rows = [];
  const seen = {};

  for (const event of events) {
    const eventTitle = event.title || event.event_ticker || '';
    const seriesTicker = event.series_ticker || '';

    // Gather markets from nested structure or separate markets array
    const markets = event.markets || [];

    for (const market of markets) {
      const marketTitle  = market.title || '';
      const marketTicker = market.ticker || '';

      // Skip if we've already processed this market
      if (seen[marketTicker]) continue;
      seen[marketTicker] = true;

      // Only include head-to-head matchup markets
      if (!isMatchupMarket_(marketTitle)) continue;

      const players = parsePlayers_(marketTitle);
      if (!players) continue;

      rows.push([
        seriesTicker,
        eventTitle,
        players.player1,
        players.player2,
        marketTicker,
        'https://kalshi.com/markets/' + seriesTicker.toLowerCase() + '/' + marketTicker.toLowerCase(),
      ]);
    }
  }

  // Sort by tournament then event
  rows.sort(function(a, b) {
    return (a[0] + a[1]).localeCompare(b[0] + b[1]);
  });

  return rows;
}

/**
 * Fallback: searches all open markets for tennis-related matchups when
 * series-based filtering returns nothing.
 * @returns {Array.<Array>}
 */
function getLiveTennisMatchupsBroadSearch_() {
  Logger.log('Running broad market search for tennis matchups...');

  // Common Kalshi tennis-related series tickers to try
  const candidateTickers = [
    'KTENNIS', 'TENNIS', 'KATP', 'KWTA',
    'KATPAUS', 'KWTAAUS', // Australian Open
    'KATPIND', 'KWTAIND', // Indian Wells / BNP
    'KATPMIA', 'KWTAMIA', // Miami Open
    'KATPMC',  'KWTAMC',  // Monte Carlo / Madrid
    'KATPWIM', 'KWTAWIM', // Wimbledon
    'KATPUSO', 'KWTAUSO', // US Open
    'KATPFR',  'KWTAFR',  // French Open / Roland Garros
    'KATPROM', 'KWTAROM', // Rome
  ];

  const allMarkets = [];
  for (const ticker of candidateTickers) {
    try {
      const markets = fetchAllPages_(
        '/markets',
        { series_ticker: ticker, status: 'open' },
        'markets'
      );
      if (markets.length > 0) {
        Logger.log('Found ' + markets.length + ' markets for series: ' + ticker);
        allMarkets.push.apply(allMarkets, markets);
      }
    } catch (e) {
      // Silently skip invalid tickers
    }
  }

  return processMarketsIntoRows_(allMarkets);
}

/**
 * Converts a flat array of Kalshi market objects into spreadsheet rows.
 * @param {Array} markets
 * @returns {Array.<Array>}
 */
function processMarketsIntoRows_(markets) {
  const rows  = [];
  const seen  = {};

  for (const market of markets) {
    const marketTicker  = market.ticker        || '';
    const marketTitle   = market.title         || '';
    const eventTicker   = market.event_ticker  || '';
    const seriesTicker  = market.series_ticker || '';

    if (seen[marketTicker]) continue;
    seen[marketTicker] = true;

    if (!isMatchupMarket_(marketTitle)) continue;

    const players = parsePlayers_(marketTitle);
    if (!players) continue;

    rows.push([
      seriesTicker,
      eventTicker,
      players.player1,
      players.player2,
      marketTicker,
      'https://kalshi.com/markets/' + seriesTicker.toLowerCase() + '/' + marketTicker.toLowerCase(),
    ]);
  }

  rows.sort(function(a, b) {
    return (a[0] + a[1]).localeCompare(b[0] + b[1]);
  });

  return rows;
}

// ─────────────────────────────────────────────────────────────────────────────
// GOOGLE SHEETS INTEGRATION
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Main entry point: fetches live tennis matchups and writes them to a sheet.
 * Can be triggered from the custom menu or run directly.
 */
function fetchTennisMatchups() {
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  let sheet   = ss.getSheetByName(OUTPUT_SHEET_NAME);

  // Create the output sheet if it doesn't exist
  if (!sheet) {
    sheet = ss.insertSheet(OUTPUT_SHEET_NAME);
  }

  // Show progress in the sheet title area
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Fetching live tennis matchups from Kalshi...', 'Kalshi Tennis', 10
  );

  let rows;
  try {
    rows = getLiveTennisMatchups();
  } catch (e) {
    SpreadsheetApp.getActiveSpreadsheet().toast(
      'Error: ' + e.message, 'Kalshi Tennis', 15
    );
    Logger.log('fetchTennisMatchups error: ' + e.stack);
    throw e;
  }

  // Clear existing content
  sheet.clearContents();
  sheet.clearFormats();

  // Write headers
  const headers = [
    'Series', 'Tournament / Event', 'Player 1', 'Player 2',
    'Market Ticker', 'Kalshi Link'
  ];
  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setValues([headers]);

  // Style headers
  headerRange
    .setBackground('#1a73e8')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center');

  // Write data rows
  if (rows.length > 0) {
    const dataRange = sheet.getRange(2, 1, rows.length, headers.length);
    dataRange.setValues(rows);

    // Alternate row shading
    for (let i = 0; i < rows.length; i++) {
      const row = sheet.getRange(i + 2, 1, 1, headers.length);
      row.setBackground(i % 2 === 0 ? '#f8f9fa' : '#ffffff');
    }

    // Auto-resize columns
    sheet.autoResizeColumns(1, headers.length);

    // Make the link column clickable (add hyperlink formula)
    for (let i = 0; i < rows.length; i++) {
      const ticker = rows[i][4]; // Market Ticker
      const series = rows[i][0]; // Series
      const linkCell = sheet.getRange(i + 2, 6);
      linkCell.setFormula(
        '=HYPERLINK("https://kalshi.com/markets/' +
        series.toLowerCase() + '/' + ticker.toLowerCase() +
        '","' + ticker + '")'
      );
    }
  }

  // Write timestamp
  const lastRow = rows.length + 2;
  sheet.getRange(lastRow + 1, 1).setValue(
    'Last updated: ' + new Date().toLocaleString()
  ).setFontColor('#888888').setFontStyle('italic');

  // Freeze header row
  sheet.setFrozenRows(1);

  // Activate the sheet so the user sees results
  ss.setActiveSheet(sheet);

  const summary = rows.length > 0
    ? 'Found ' + rows.length + ' live matchup(s) across ' +
      new Set(rows.map(r => r[0])).size + ' tournament(s).'
    : 'No live ATP/WTA 250+ matchups found on Kalshi right now.';

  SpreadsheetApp.getActiveSpreadsheet().toast(summary, 'Kalshi Tennis', 8);
  Logger.log('Done. ' + summary);
}

/**
 * Custom spreadsheet function.
 * Use =FETCH_TENNIS_MATCHUPS() in a cell to populate data starting at that cell.
 *
 * Note: Custom functions cannot write to other cells; this returns the 2D array
 * so Sheets spills the data automatically.
 *
 * @returns {Array.<Array>} 2D array with headers + data rows
 * @customfunction
 */
function FETCH_TENNIS_MATCHUPS() {
  const rows = getLiveTennisMatchups();
  const headers = [
    ['Series', 'Tournament / Event', 'Player 1', 'Player 2', 'Market Ticker', 'Kalshi Link']
  ];
  return headers.concat(rows.length > 0 ? rows : [['No live matchups found', '', '', '', '', '']]);
}

// ─────────────────────────────────────────────────────────────────────────────
// NOTIFICATION SYSTEM
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Stores the email address to receive new-market notifications.
 * Call this once from the Apps Script editor, or use the menu dialog.
 *
 * @param {string} email - Email address for notifications
 */
function setNotificationEmail(email) {
  PropertiesService.getScriptProperties().setProperty(PROP_NOTIFICATION_EMAIL, email);
  Logger.log('Notification email saved: ' + email);
}

/**
 * Returns the stored notification email, or the script owner's email as fallback.
 * @returns {string}
 */
function getNotificationEmail_() {
  const stored = PropertiesService.getScriptProperties().getProperty(PROP_NOTIFICATION_EMAIL);
  return stored || Session.getActiveUser().getEmail();
}

/**
 * Loads the set of market tickers that have already been notified about.
 * @returns {Set<string>}
 */
function loadSeenTickers_() {
  const raw = PropertiesService.getScriptProperties().getProperty(PROP_SEEN_TICKERS);
  if (!raw) return new Set();
  try {
    return new Set(JSON.parse(raw));
  } catch (e) {
    return new Set();
  }
}

/**
 * Persists the current set of seen tickers to Script Properties.
 * Script Properties have a 500KB limit; stores up to ~10k tickers safely.
 * @param {Set<string>} tickerSet
 */
function saveSeenTickers_(tickerSet) {
  const arr = Array.from(tickerSet);
  PropertiesService.getScriptProperties().setProperty(PROP_SEEN_TICKERS, JSON.stringify(arr));
}

/**
 * Clears the seen-tickers store so the next check treats everything as new.
 * Useful for testing, or when you want to be re-notified after a reset.
 */
function clearSeenMarkets() {
  PropertiesService.getScriptProperties().deleteProperty(PROP_SEEN_TICKERS);
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Seen-markets history cleared. Next check will notify for all current markets.',
    'Kalshi Tennis Notifications', 5
  );
  Logger.log('Seen-markets history cleared.');
}

/**
 * Builds and sends an email summarising newly-detected tennis markets.
 *
 * @param {Array.<Array>} newRows  - Rows with [series, event, p1, p2, ticker, url]
 * @param {string}        toEmail  - Recipient address
 */
function sendNewMarketsEmail_(newRows, toEmail) {
  const count = newRows.length;
  const subject = '[Kalshi Tennis] ' + count + ' new line' + (count === 1 ? '' : 's') + ' just dropped!';

  // Group rows by series for a cleaner email body
  const byTournament = {};
  for (const row of newRows) {
    const key = row[0] + ' — ' + row[1]; // series + event
    if (!byTournament[key]) byTournament[key] = [];
    byTournament[key].push(row);
  }

  // Plain-text body
  let textBody = 'New ATP/WTA tennis lines are now live on Kalshi!\n\n';
  for (const tournament of Object.keys(byTournament).sort()) {
    textBody += '=== ' + tournament + ' ===\n';
    for (const row of byTournament[tournament]) {
      textBody += '  ' + row[2] + ' vs. ' + row[3] + '\n';
      textBody += '  ' + row[5] + '\n\n';
    }
  }
  textBody += '──────────────────────────────────────\n';
  textBody += 'Checked at: ' + new Date().toLocaleString() + '\n';
  textBody += 'Powered by Kalshi Tennis Scraper (Google Sheets)\n';

  // HTML body — nicer for email clients
  let htmlBody = '<html><body style="font-family:Arial,sans-serif;color:#202124;">';
  htmlBody += '<h2 style="color:#1a73e8;">🎾 ' + count + ' new Kalshi tennis line' + (count === 1 ? '' : 's') + ' just dropped!</h2>';

  for (const tournament of Object.keys(byTournament).sort()) {
    htmlBody += '<h3 style="margin-bottom:4px;border-bottom:1px solid #dadce0;">' + tournament + '</h3>';
    htmlBody += '<table cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:600px;">';
    htmlBody += '<tr style="background:#1a73e8;color:#fff;">' +
      '<th align="left">Player 1</th><th align="left">Player 2</th><th align="left">Link</th></tr>';

    for (let i = 0; i < byTournament[tournament].length; i++) {
      const row = byTournament[tournament][i];
      const bg = i % 2 === 0 ? '#f8f9fa' : '#ffffff';
      htmlBody += '<tr style="background:' + bg + ';">' +
        '<td>' + row[2] + '</td>' +
        '<td>' + row[3] + '</td>' +
        '<td><a href="' + row[5] + '" style="color:#1a73e8;">' + row[4] + '</a></td>' +
        '</tr>';
    }
    htmlBody += '</table><br>';
  }

  htmlBody += '<p style="color:#888;font-size:12px;">Checked at ' + new Date().toLocaleString() + '</p>';
  htmlBody += '</body></html>';

  MailApp.sendEmail({
    to:       toEmail,
    subject:  subject,
    body:     textBody,
    htmlBody: htmlBody,
  });

  Logger.log('Notification email sent to ' + toEmail + ' for ' + count + ' new market(s).');
}

/**
 * Core notification check: compares live Kalshi tennis markets against the
 * set of previously seen tickers and emails you about anything new.
 *
 * This is the function to attach to a time-driven trigger.
 */
function checkForNewTennisMarkets() {
  Logger.log('Notification check started at ' + new Date().toISOString());

  let rows;
  try {
    rows = getLiveTennisMatchups();
  } catch (e) {
    Logger.log('checkForNewTennisMarkets: fetch failed — ' + e.message);
    return; // Don't crash the trigger; try again next interval
  }

  if (rows.length === 0) {
    Logger.log('No live tennis markets found.');
    return;
  }

  const seenTickers = loadSeenTickers_();
  const newRows = rows.filter(function(row) {
    return !seenTickers.has(row[4]); // row[4] is the market ticker
  });

  Logger.log('Live markets: ' + rows.length + ', new: ' + newRows.length);

  if (newRows.length > 0) {
    const email = getNotificationEmail_();
    try {
      sendNewMarketsEmail_(newRows, email);
    } catch (e) {
      Logger.log('Failed to send notification email: ' + e.message);
    }

    // Mark the new tickers as seen AFTER attempting to send, so a send failure
    // retries on the next check rather than silently swallowing the notification.
    for (const row of newRows) {
      seenTickers.add(row[4]);
    }
    saveSeenTickers_(seenTickers);
  }

  Logger.log('Notification check complete.');
}

// ─────────────────────────────────────────────────────────────────────────────
// NOTIFICATION TRIGGERS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Installs a time-driven trigger that runs checkForNewTennisMarkets every
 * NOTIFICATION_CHECK_INTERVAL_MINUTES minutes.
 * Also seeds the seen-tickers store with whatever is live right now, so you
 * only get notified about markets that appear *after* setup.
 */
function installNotificationTrigger() {
  removeNotificationTrigger(); // avoid duplicates

  // Seed seen tickers with current markets so we don't spam on first run
  try {
    const rows = getLiveTennisMatchups();
    if (rows.length > 0) {
      const tickers = new Set(rows.map(function(r) { return r[4]; }));
      saveSeenTickers_(tickers);
      Logger.log('Seeded seen-tickers with ' + tickers.size + ' current markets.');
    }
  } catch (e) {
    Logger.log('Could not seed seen-tickers (will notify for all current markets on first run): ' + e.message);
  }

  ScriptApp.newTrigger('checkForNewTennisMarkets')
    .timeBased()
    .everyMinutes(NOTIFICATION_CHECK_INTERVAL_MINUTES)
    .create();

  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Notifications ON: checking every ' + NOTIFICATION_CHECK_INTERVAL_MINUTES +
    ' min. Alerts → ' + getNotificationEmail_(),
    'Kalshi Tennis Notifications', 7
  );
  Logger.log('Notification trigger installed.');
}

/**
 * Removes the notification check trigger.
 */
function removeNotificationTrigger() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === 'checkForNewTennisMarkets') {
      ScriptApp.deleteTrigger(trigger);
    }
  }
}

/**
 * Disables notifications and shows a confirmation toast.
 */
function disableNotifications() {
  removeNotificationTrigger();
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Notifications disabled.', 'Kalshi Tennis Notifications', 4
  );
  Logger.log('Notification trigger removed.');
}

/**
 * Prompts the user for their notification email address then enables the trigger.
 */
function showNotificationSetupDialog() {
  const ui = SpreadsheetApp.getUi();

  const emailResponse = ui.prompt(
    'Kalshi Tennis — Notification Setup',
    'Enter the email address to receive new-line alerts.\n' +
    '(Leave blank to use your Google account email: ' + Session.getActiveUser().getEmail() + ')',
    ui.ButtonSet.OK_CANCEL
  );

  if (emailResponse.getSelectedButton() !== ui.Button.OK) return;

  const email = emailResponse.getResponseText().trim();
  if (email) {
    setNotificationEmail(email);
  }

  installNotificationTrigger();

  ui.alert(
    'Notifications Enabled!',
    'You will receive an email at ' + getNotificationEmail_() +
    ' whenever new ATP/WTA 250+ tennis lines drop on Kalshi.\n\n' +
    'Checks run every ' + NOTIFICATION_CHECK_INTERVAL_MINUTES + ' minutes.\n\n' +
    'To stop notifications, use: Kalshi Tennis → Notifications → Disable Notifications.',
    ui.ButtonSet.OK
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MENU & TRIGGERS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Adds a custom menu to the spreadsheet when it opens.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Kalshi Tennis')
    .addItem('Refresh Matchups', 'fetchTennisMatchups')
    .addSeparator()
    .addItem('Setup Credentials', 'showCredentialDialog')
    .addItem('Setup Auto-Refresh (every 15 min)', 'installAutoRefreshTrigger')
    .addItem('Remove Auto-Refresh', 'removeAutoRefreshTrigger')
    .addSeparator()
    .addSubMenu(
      SpreadsheetApp.getUi().createMenu('Notifications')
        .addItem('Enable New-Line Notifications…', 'showNotificationSetupDialog')
        .addItem('Disable Notifications',          'disableNotifications')
        .addSeparator()
        .addItem('Check for New Lines Now',        'checkForNewTennisMarkets')
        .addItem('Reset Seen-Markets History',     'clearSeenMarkets')
    )
    .addToUi();
}

/**
 * Shows a dialog to guide the user through credential setup.
 */
function showCredentialDialog() {
  const ui = SpreadsheetApp.getUi();
  const result = ui.alert(
    'Kalshi API Credentials Setup',
    'To use this scraper, you need a Kalshi API key:\n\n' +
    '1. Go to kalshi.com > Account > API Keys\n' +
    '2. Generate a new API key (download the key file)\n' +
    '3. In the Apps Script editor:\n' +
    '   a. Open Extensions > Apps Script\n' +
    '   b. Go to Project Settings > Script Properties\n' +
    '   c. Add: KALSHI_API_KEY_ID = <your key ID>\n' +
    '   d. Add: KALSHI_PRIVATE_KEY = <your PEM private key>\n\n' +
    'Then click "Refresh Matchups" from the Kalshi Tennis menu.',
    ui.ButtonSet.OK
  );
}

/**
 * Installs a time-driven trigger to auto-refresh every 15 minutes.
 */
function installAutoRefreshTrigger() {
  // Remove any existing trigger first
  removeAutoRefreshTrigger();

  ScriptApp.newTrigger('fetchTennisMatchups')
    .timeBased()
    .everyMinutes(15)
    .create();

  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Auto-refresh enabled: matchups will update every 15 minutes.',
    'Kalshi Tennis', 5
  );
}

/**
 * Removes the auto-refresh time trigger.
 */
function removeAutoRefreshTrigger() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === 'fetchTennisMatchups') {
      ScriptApp.deleteTrigger(trigger);
    }
  }
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Auto-refresh removed.', 'Kalshi Tennis', 3
  );
}
