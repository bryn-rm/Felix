import Link from "next/link";
import { archivo, newsreader } from "./fonts";
import styles from "./PublicHomepage.module.css";

export default function PublicHomepage({ requestAccessUrl }: { requestAccessUrl?: string }) {
  return (
    <div className={`${styles.page} ${newsreader.variable} ${archivo.variable}`}>
      <a className={styles.skipLink} href="#main">Skip to content</a>
      <div className={styles.sheet}>
        <header className={styles.header}>
          <Link href="/" className={styles.wordmark} aria-label="Felix homepage">Felix</Link>
          <Link href="/login" className={styles.loginLink}>Log in</Link>
        </header>

        <main id="main" tabIndex={-1}>
          <section className={styles.hero} aria-labelledby="hero-title">
            <p className={styles.eyebrow}>AI assistant · Private beta</p>
            <h1 id="hero-title" className={styles.headline}>Email, meetings,<br />{" "}and what comes next.</h1>
            <p className={styles.intro}>
              Felix works with Gmail, Google Calendar, and your meetings to help
              you draft replies, keep track of decisions, and follow up.
            </p>
            <div className={styles.actions}>
              {requestAccessUrl ? (
                <a className={styles.primaryLink} href={requestAccessUrl} target="_blank" rel="noopener noreferrer">
                  Request access <span aria-hidden="true">↗</span>
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              ) : (
                <Link className={styles.primaryLink} href="/login">Log in to Felix</Link>
              )}
            </div>
          </section>

          <section className={styles.features} aria-labelledby="features-title">
            <h2 id="features-title">What Felix helps with</h2>
            <dl className={styles.featureList}>
              <div>
                <dt>Email &amp; calendar</dt>
                <dd>Review priority emails, draft replies, and prepare for upcoming meetings.</dd>
              </div>
              <div>
                <dt>Meeting notes</dt>
                <dd>Record a meeting or add your own notes. Get a summary of the discussion, decisions, and action items.</dd>
              </div>
              <div>
                <dt>Follow-ups &amp; projects</dt>
                <dd>Track what you owe and what you’re waiting on. Keep related emails, meetings, and decisions together in a project.</dd>
              </div>
            </dl>
          </section>

          <section className={styles.control} aria-labelledby="control-title">
            <h2 id="control-title">You review before Felix acts.</h2>
            <p>Sending emails and creating calendar events require your approval.
              You can disconnect your Google account at any time.</p>
          </section>
        </main>

        <footer className={styles.footer}>
          <span>© Felix 2026</span>
          <span>Private beta</span>
        </footer>
      </div>
    </div>
  );
}
