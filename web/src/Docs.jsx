const TOC = [
  ['start', 'Getting Started'],
  ['accounts', 'Accounts & Modes'],
  ['proxies', 'Proxies'],
  ['automation', 'Automation'],
  ['autoreply', 'Twitter Auto Reply'],
  ['activity', 'Activity Panel'],
  ['settings', 'Settings'],
  ['tips', 'Tips & Troubleshooting'],
];

function Section({ id, title, children }) {
  return (
    <section id={id} className="doc-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function P({ children }) { return <p>{children}</p>; }
function LI({ children }) { return <li>{children}</li>; }

export default function Docs() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="prompt">
          <span className="p-user">docs</span>
          <span className="p-at">@</span>
          <span className="p-host">mhfcorp</span>
          <span className="p-sep">:</span>
          <span className="p-path">~/docs</span>
          <span className="p-end">#</span>
        </div>
        <div className="status"><span className="badge badge-idle">documentation</span></div>
      </header>

      <main className="docs">
        <nav className="docs-toc">
          {TOC.map(([id, label]) => (
            <a key={id} href={`#${id}`}>{label}</a>
          ))}
        </nav>

        <div className="docs-content">
          <div className="docs-intro">
            <h1>Documentation</h1>
            <p>Everything you need to run XBOT — accounts, proxies, automation, and debugging. Skim the left index, or read top to bottom.</p>
          </div>

          <Section id="start" title="Getting Started">
            <P>Log in with the UI password. From the <strong>XBOT</strong> dashboard you add accounts, watch live activity, and control the scheduler.</P>
            <ul>
              <LI><strong>Add an account:</strong> give it a name, the <code>@username</code>, and the two session cookies <code>auth_token</code> and <code>ct0</code> (copied from your browser's x.com cookies). Set daily quotas and a mode, then save.</LI>
              <LI><strong>Activate it</strong> with the toggle in the accounts table. Only active accounts are run.</LI>
              <LI><strong>Turn the schedule on</strong> in Settings. The scheduler starts each account's chain within the day window.</LI>
              <LI>Use <strong>Dry run</strong> first — it simulates everything except the final click, so you can validate the flow safely.</LI>
            </ul>
          </Section>

          <Section id="accounts" title="Accounts & Modes">
            <P>Each account has a <strong>mode</strong> that decides where it finds tweets to engage:</P>
            <ul>
              <LI><code>search</code> — searches your keywords on the <strong>Latest</strong> tab and engages the results.</LI>
              <LI><code>timeline</code> — engages the account's home timeline.</LI>
              <LI><code>search by popularity</code> — uses X advanced search with <code>min_faves</code> (min likes) and optional <code>min_replies</code> to target popular, relevant tweets. Pick the <strong>Latest</strong> or <strong>Top</strong> tab. A min-likes threshold is required.</LI>
            </ul>
            <P>Keywords are comma-separated. Tweets already engaged are remembered per account and skipped, so the same tweet isn't liked twice.</P>
          </Section>

          <Section id="proxies" title="Proxies">
            <P>Route an account's traffic through a proxy so X sees a different exit IP. Add one from the <strong>Proxy</strong> page, then bind it to an account in the account form.</P>
            <ul>
              <LI><strong>Paste an endpoint:</strong> <code>user:pass@host:port</code>, <code>socks5://user:pass@host:port</code>, or <code>host:port</code>.</LI>
              <LI><strong>Test connection before adding</strong> — it must pass HTTPS, because the bot browses X over HTTPS.</LI>
              <LI><strong>HTTP proxies</strong> authenticate natively in the browser.</LI>
              <LI><strong>SOCKS5 with username/password is not supported by the browser</strong> (a Chromium limit). Use the provider's HTTP endpoint, or an IP-whitelisted SOCKS5 proxy.</LI>
              <LI>The exit IP used for each run is shown in the Activity panel.</LI>
            </ul>
            <P>Proxy health is checked periodically; dead proxies are flagged red. A “Malformed reply” error usually means a scheme mismatch — match the provider's protocol (HTTP vs SOCKS5).</P>
          </Section>

          <Section id="automation" title="Automation">
            <P>The scheduler ticks every minute and runs one action at a time per account, spaced across the day window to look human.</P>
            <ul>
              <LI><strong>Day window</strong> (Settings, Jakarta time) bounds when actions run. Quotas are per day.</LI>
              <LI>Each <strong>post / like / retweet / reply</strong> runs in its own short browser session; the next is scheduled at a randomized gap.</LI>
              <LI>Before every like, retweet, or reply, a <strong>random 3–70 second delay</strong> runs after a tweet is selected — visible as a live countdown in Activity.</LI>
              <LI><strong>Run now</strong> queues the next due action immediately; <strong>Replan</strong> rebuilds today's plan from remaining quota.</LI>
              <LI><strong>Posting and AI replies</strong> need an OpenAI key (see Settings).</LI>
            </ul>
          </Section>

          <Section id="autoreply" title="Twitter Auto Reply">
            <P>Assign warm-up accounts to the <strong>Twitter Auto Reply</strong> automation. Each assigned account runs independently: it searches X for its own keywords, filters tweets by engagement, and posts AI replies written with its own system prompt.</P>
            <ul>
              <LI><strong>Per-account config:</strong> keywords + exclusions, language, sort (Latest/Top), min likes/retweets/replies, max tweet age, search interval, tweets per cycle (the only reply cap), cooldown, banned words, and the system prompt.</LI>
              <LI><strong>Reset</strong> (circular-arrow button) clears an account's tweet history — every tweet becomes eligible for replies again.</LI>
              <LI><strong>Filtering:</strong> X advanced search does the coarse filtering (<code>min_faves</code>, <code>min_retweets</code>, <code>min_replies</code>, <code>lang</code>, <code>since</code>); the bot re-checks every threshold client-side before replying.</LI>
              <LI><strong>Duplicate protection:</strong> every processed tweet id is stored — an account never replies to the same tweet twice.</LI>
              <LI><strong>Reply validation:</strong> max length, banned words, and duplicate responses are rejected before posting.</LI>
              <LI><strong>Status:</strong> draft → active → paused / stopped / error. Start, pause, resume, stop, edit, or run a cycle now per account — one account's error never stops the others.</LI>
              <LI><strong>OpenAI key required</strong> (Settings) — replies are generated with the account's own system prompt.</LI>
              <LI><strong>Live activity</strong> at the bottom of the page streams every cycle, reply, and skip across all accounts — the warm-up activity feed stays warm-up only.</LI>
              <LI><strong>Dry run</strong> (Settings) applies here too: replies are generated and recorded as <em>skipped</em>, never posted. The page shows a warning banner while it's on.</LI>
            </ul>
          </Section>

          <Section id="activity" title="Activity Panel">
            <P>The live feed on the dashboard shows every warm-up run across all accounts — no refresh needed. Auto-reply cycles don't appear here; they have their own live activity feed on the Twitter Auto Reply page.</P>
            <ul>
              <LI><strong>In Progress</strong> (yellow) while a run is active, with a live countdown of the pre-action delay.</LI>
              <LI><strong>Success</strong> (green) when the action completes; <strong>Failed</strong> (red) or <strong>Skipped</strong> (muted) otherwise.</LI>
              <LI>Each row shows the account, the exit IP used, what it did, and the trigger.</LI>
              <LI><strong>Click any row to expand:</strong> the full error message, context (account, trigger, exit IP, timestamps), the counts, and the run-log tail with the traceback — everything needed to find the root cause.</LI>
            </ul>
          </Section>

          <Section id="settings" title="Settings">
            <P>Organized into three cards. Save is disabled until you change something; an <em>unsaved changes</em> indicator shows the state.</P>
            <ul>
              <LI><strong>Automation:</strong> schedule active, dry run, headless browser, day window.</LI>
              <LI><strong>Engagement:</strong> like/retweet probabilities, min/max delay between actions, and reply min-likes (replies target popular tweets on the Latest tab).</LI>
              <LI><strong>OpenAI:</strong> model, API key, and the system prompts for posts and replies. Leave the key blank to keep the saved one. Required for posting and AI replies.</LI>
            </ul>
          </Section>

          <Section id="tips" title="Tips & Troubleshooting">
            <ul>
              <LI><strong>Login fails?</strong> X often flags logins from datacenter IPs. Use a proxy, or open the run's expanded detail to see the log and screenshots.</LI>
              <LI><strong>No actions running?</strong> Check: the account is active, the schedule is on, daily quota remains, and the current time is inside the day window.</LI>
              <LI><strong>Proxy won't connect?</strong> Confirm the scheme matches the provider; SOCKS5+auth can't be used by the browser — use HTTP.</LI>
              <LI><strong>Start with Dry run</strong> to confirm the flow, then turn it off to engage for real.</LI>
              <LI><strong>Need detail?</strong> Every failed run keeps a full log — expand it in Activity.</LI>
            </ul>
          </Section>
        </div>
      </main>
    </div>
  );
}
