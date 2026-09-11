import Link from "next/link";
import { archivo, caveat, mono, newsreader } from "./fonts";
import styles from "./PublicHomepage.module.css";

const thread = [
  {
    id: "launch-risk",
    number: "I",
    kind: "Meeting · Monday, 09:30",
    title: "A risk enters the room.",
    description: "The website launch is close. In the planning call, Maya flags an unresolved copy approval. Felix captures the discussion and keeps the risk in the meeting summary.",
    label: "From the meeting summary",
    excerpt: "Launch copy is still awaiting approval. Friday’s release depends on a decision from Maya.",
    note: "Website Launch / Planning",
  },
  {
    id: "launch-approval",
    number: "II",
    kind: "Correspondence · Monday, 14:12",
    title: "The answer arrives elsewhere.",
    description: "Maya approves the copy by email. Felix keeps the correspondence available, so the answer can be found alongside the question that started it.",
    label: "Email from Maya",
    excerpt: "The revised copy is approved. Please use the version from this morning.",
    note: "Re: Website launch copy",
  },
  {
    id: "launch-commitment",
    number: "III",
    kind: "Commitment · Tuesday, 10:00",
    title: "“I’ll do it” becomes a record.",
    description: "You promise to send Sam the final launch checklist. Felix picks up the action from the meeting summary and records a commitment, with the source and due date close at hand.",
    label: "Your commitment",
    excerpt: "Send Sam the final launch checklist by Thursday.",
    note: "Owed by you / Open",
  },
  {
    id: "launch-decision",
    number: "IV",
    kind: "Meeting · Wednesday, 11:00",
    title: "Then the decision changes.",
    description: "The copy is ready; the checkout is not. A later meeting moves the launch to Monday for another round of testing. Felix’s summary captures the new decision and why it changed.",
    label: "From the launch review",
    excerpt: "Move the launch from Friday to Monday. Allow time for checkout testing; copy approval still stands.",
    note: "Website Launch / Review",
  },
];

function RequestAccess({ href }: { href: string }) {
  return (
    <a className={styles.primaryLink} href={href} target="_blank" rel="noopener noreferrer">
      Request access <span aria-hidden="true">↗</span>
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

export default function PublicHomepage({ requestAccessUrl }: { requestAccessUrl?: string }) {
  return (
    <div className={`${styles.page} ${newsreader.variable} ${archivo.variable} ${mono.variable} ${caveat.variable}`}>
      <a className={styles.skipLink} href="#main">Skip to content</a>
      <div className={styles.sheet}>
        <header className={styles.header}>
          <Link href="/" className={styles.wordmark} aria-label="Felix homepage">Feli<em>x</em></Link>
          <nav aria-label="Account" className={styles.headerLinks}>
            <Link href="/login" className={styles.loginLink}>Log in</Link>
            {requestAccessUrl && <RequestAccess href={requestAccessUrl} />}
          </nav>
        </header>

        <main id="main" tabIndex={-1} className={styles.main}>
          <div className={styles.masthead}>
            <span>Dossier No. 01</span>
            <span>The working memory of your work</span>
            <span>Private beta</span>
          </div>

          <section className={styles.hero} aria-labelledby="hero-title">
            <div className={styles.heroCopy}>
              <p className={styles.eyebrow}>On the matter of keeping the thread</p>
              <h1 id="hero-title" className={styles.headline}>
                <span>I read the</span>{" "}<span>four hundred.</span>{" "}
                <em>You read the nine.</em>
              </h1>
              <div className={styles.heroIntro}>
                <p className={styles.lede}><span className={styles.dropCap}>I</span>’m Felix. I maintain the working memory of your work: what was discussed, decided, changed, and promised.</p>
                <p>Across email, meetings, and the work in between, I carry the context forward — so you can see what matters now and remember why.</p>
              </div>
              <div className={styles.actions}>
                {requestAccessUrl && <RequestAccess href={requestAccessUrl} />}
                <a className={styles.secondaryLink} href="#dossier">Read the dossier <span aria-hidden="true">↓</span></a>
              </div>
            </div>

            <section className={styles.morningPage} aria-labelledby="morning-title">
              <div className={styles.specimenTop}><span>Daily briefing</span><span>01 / AM</span></div>
              <h2 id="morning-title">The Morning<br /><em>Page.</em></h2>
              <p className={styles.morningDate}>Thursday · Before the day begins</p>
              <dl className={styles.briefing}>
                <div><dt>Changed</dt><dd>Website Launch moved to Monday. Checkout testing needs more time.</dd></div>
                <div><dt>Resolved</dt><dd>Maya approved the copy. The answer is in Monday’s email.</dd></div>
                <div><dt>Owed</dt><dd>You promised Sam the final checklist. <mark>That’s today.</mark></dd></div>
                <div><dt>Next</dt><dd>09:30 launch check-in. Bring the revised date and the open testing question.</dd></div>
              </dl>
              <p className={styles.annotation}>The day ahead, with the past attached.</p>
              <p className={styles.specimenLabel}>Illustrative briefing · Fictional work</p>
            </section>
          </section>

          <ul className={styles.indexStrip} role="list" aria-label="Context Felix works with">
            <li>Email &amp; correspondence</li><li>Meetings &amp; Live Assist</li><li>Commitments &amp; Projects</li><li>Memory &amp; retrieval</li>
          </ul>

          <section id="dossier" className={styles.dossier} aria-labelledby="dossier-title">
            <div className={styles.sectionHeading}>
              <div><p className={styles.eyebrow}>One project. Several conversations.</p><h2 id="dossier-title">The work moves.<br /><em>The thread stays.</em></h2></div>
              <div className={styles.sectionAside}><p>An email here. A promise there. A decision that changes three days later. Follow one launch through Felix.</p><p className={styles.specimenLabel}>Case file / Website Launch<br />Illustrative examples throughout</p></div>
            </div>
            <ol className={styles.thread} role="list" aria-label="Website Launch timeline">
              {thread.map((entry) => (
                <li key={entry.id} id={entry.id} className={styles.threadEntry}>
                  <span className={styles.roman} aria-hidden="true">{entry.number}</span>
                  <div className={styles.entryCopy}><p className={styles.eyebrow}>{entry.kind}</p><h3>{entry.title}</h3><p>{entry.description}</p></div>
                  <figure className={styles.excerpt}><figcaption>{entry.label}</figcaption><blockquote>“{entry.excerpt}”</blockquote><p className={styles.sourceNote}>{entry.note}</p></figure>
                </li>
              ))}
            </ol>
          </section>

          <section className={styles.projectSection} aria-labelledby="project-title">
            <div className={styles.projectIntro}>
              <p className={styles.eyebrow}>V / Projects &amp; project context</p>
              <h2 id="project-title">A place for<br /><em>what stands now.</em></h2>
              <p>Bring the emails, meetings, and commitments into your Website Launch Project. Felix helps find related sources; you choose what belongs and confirm the decisions.</p>
              <p>As the work changes, keep the current decision alongside the earlier one. Generate an update from the linked evidence when you need the latest picture.</p>
              <p className={styles.annotation}>A current record. A visible history.</p>
            </div>
            <article className={styles.projectFile} aria-labelledby="project-file-title">
              <div className={styles.specimenTop}><span>Project record / Specimen</span><span>Private</span></div>
              <h3 id="project-file-title">Website Launch</h3>
              <p className={styles.fileSubtitle}>Selected sources. Confirmed context.</p>
              <dl className={styles.projectRecords}>
                <div><dt>Current decision</dt><dd><p>Launch Monday, after checkout testing.</p><a href="#launch-decision">Source: Wednesday’s review <span aria-hidden="true">↑</span></a></dd></div>
                <div><dt>Earlier decision</dt><dd><p className={styles.superseded}>Launch Friday.</p><span className={styles.sourceNote}>Superseded · Kept in the history</span></dd></div>
                <div><dt>Approval</dt><dd><p>Revised copy approved by Maya.</p><a href="#launch-approval">Source: Monday’s email <span aria-hidden="true">↑</span></a></dd></div>
                <div><dt>Open commitment</dt><dd><p>Send Sam the final checklist.</p><a href="#launch-commitment">Source: Tuesday’s meeting <span aria-hidden="true">↑</span></a></dd></div>
              </dl>
              <p className={styles.fileFootnote}>You link the sources and confirm the record. Felix keeps the context together.</p>
            </article>
          </section>

          <section className={styles.assistSection} aria-labelledby="assist-title">
            <div className={styles.assistIntro}>
              <p className={styles.eyebrow}>VI / Live Assist &amp; memory</p>
              <h2 id="assist-title">The past, present<br /><em>when you need it.</em></h2>
              <p>At the next check-in, someone asks why the date moved. With Live Assist enabled, Felix can draw on available meeting context, correspondence, commitments, and memory to help you answer.</p>
              <p>Ask a question during the conversation. Bring earlier context back into view without starting the search from scratch.</p>
            </div>
            <div className={styles.assistExample}>
              <p className={styles.specimenTop}>At the next launch check-in / Specimen</p>
              <p className={styles.question}>“Wasn’t Friday the plan?”</p>
              <div className={styles.assistAnswer}>
                <p className={styles.eyebrow}>Felix / Earlier context</p>
                <p>Yes. In Wednesday’s review, the launch moved to Monday to allow for checkout testing. Maya’s copy approval still stands.</p>
                <a href="#launch-decision" className={styles.sourceLink}>Revisit the earlier decision <span aria-hidden="true">↑</span></a>
              </div>
              <p className={styles.annotation}>You stay in the conversation.</p>
            </div>
          </section>

          <section className={styles.trustSection} aria-labelledby="trust-title">
            <div><p className={styles.eyebrow}>The terms of the relationship</p><h2 id="trust-title">Your context.<br /><em>Your say.</em></h2><p className={styles.trustIntro}>A working memory is a position of trust. You keep control of what Felix can access and what goes out in your name.</p></div>
            <dl className={styles.trustPrinciples}>
              <div><dt>01 / Scoped to you</dt><dd>Your personal context stays within your account.</dd></div>
              <div><dt>02 / Access is revocable</dt><dd>You can disconnect your Google account and revoke access.</dd></div>
              <div><dt>03 / Approval before action</dt><dd>Sending mail and creating calendar events require your approval.</dd></div>
              <div><dt>04 / Back to the source</dt><dd>Linked project context retains its supporting sources, so you can check what was said and how a decision changed.</dd></div>
            </dl>
          </section>

          <section className={styles.closing} aria-labelledby="closing-title">
            <div><p className={styles.eyebrow}>Felix / Private beta</p><h2 id="closing-title">Pick up where<br /><em>the work left off.</em></h2><p>A little less reconstructing. A little more moving forward.</p><div className={styles.actions}>{requestAccessUrl && <RequestAccess href={requestAccessUrl} />}<Link className={styles.secondaryLink} href="/login">Log in <span aria-hidden="true">→</span></Link></div></div>
            <div className={styles.signoff}><p className={styles.annotation}>You keep the judgement.<br />I keep the thread.</p><span aria-hidden="true">F<em>x</em></span></div>
          </section>
        </main>
        <footer className={styles.footer}><span>© Felix 2026</span><span>Dossier No. 01</span><span>The working memory of your work</span></footer>
      </div>
    </div>
  );
}
