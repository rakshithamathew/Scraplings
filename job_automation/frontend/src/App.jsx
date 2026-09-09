import { useCallback, useEffect, useRef, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const FILTERS = ["ALL", "QUALIFIED", "APPLIED", "EMAIL_FOUND", "EMAIL_SENT"];
const PAGE_SIZES = [5, 10, 12];
const SCORE_FILTERS = [
  { label: "All scores", value: "" },
  { label: "60+", value: "60" },
  { label: "70+", value: "70" },
  { label: "80+", value: "80" },
  { label: "90+", value: "90" },
];
const EMPTY_STATS = { total_jobs: 0, qualified: 0, applied: 0, contacts_found: 0, emails_sent: 0, linkedin_messages_sent: 0 };
const CONNECTION_PLATFORMS = ["linkedin", "naukri", "indeed"];

async function api(path, options = {}) {
  const headers = options.body instanceof FormData
    ? { ...options.headers }
    : { "Content-Type": "application/json", ...options.headers };
  const response = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch {
      // Keep the status-based fallback for non-JSON responses.
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function formatStatus(status) {
  return status.replaceAll("_", " ");
}

function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" })
    .format(new Date(value));
}

function App() {
  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState(EMPTY_STATS);
  const [resume, setResume] = useState(null);
  const [connections, setConnections] = useState([]);
  const [filter, setFilter] = useState("ALL");
  const [minimumScore, setMinimumScore] = useState("");
  const [locationFilter, setLocationFilter] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSizeChoice, setPageSizeChoice] = useState("AUTO");
  const [autoPageSize, setAutoPageSize] = useState(5);
  const [busy, setBusy] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState(() => new Set());
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [draftPreview, setDraftPreview] = useState(null);
  const fileInputRef = useRef(null);
  const tableViewportRef = useRef(null);

  const refresh = useCallback(async (activeFilter = filter) => {
    setBusy("refresh");
    setError("");
    try {
      const [nextJobs, nextStats, nextResume, nextConnections, outreach] = await Promise.all([
        api("/jobs?limit=2000"),
        api("/stats"),
        api("/resume").catch((requestError) => requestError.status === 404 ? null : Promise.reject(requestError)),
        api("/connections"),
        api("/dashboard/outreach"),
      ]);
      const contacts = new Map(outreach.jobs.map((row) => [row.job_id, row]));
      setJobs(nextJobs.map((job) => ({ ...job, ...contacts.get(job.id) })));
      setStats({ ...nextStats, contacts_found: outreach.contacts_found, emails_sent: outreach.emails_sent, linkedin_messages_sent: outreach.linkedin_messages_sent });
      setResume(nextResume);
      setConnections(nextConnections);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy("");
    }
  }, [filter]);

  useEffect(() => { refresh(filter); }, [filter, refresh]);

  useEffect(() => {
    if (!connections.some((connection) => connection.status === "connecting")) return undefined;
    const timer = window.setInterval(() => {
      api("/connections").then(setConnections).catch(() => {});
    }, 2500);
    return () => window.clearInterval(timer);
  }, [connections]);

  const locationKey = locationFilter.trim().toLocaleLowerCase();
  const filteredJobs = jobs.filter((job) => {
    const statusMatches = filter === "ALL" || (filter === "EMAIL_FOUND" ? Boolean(job.public_work_email)
      : filter === "EMAIL_SENT" ? job.email_status === "SENT" : job.status === filter);
    const scoreMatches = minimumScore === ""
      || (job.match_score != null && job.match_score >= Number(minimumScore));
    const locationMatches = !locationKey
      || (job.location || "").toLocaleLowerCase().includes(locationKey);
    return statusMatches && scoreMatches && locationMatches;
  });
  const pageSize = pageSizeChoice === "AUTO" ? autoPageSize : Number(pageSizeChoice);
  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / pageSize));
  const pageStart = (currentPage - 1) * pageSize;
  const visibleJobs = filteredJobs.slice(pageStart, pageStart + pageSize);
  const selectableVisibleJobs = visibleJobs.filter((job) =>
    job.status === "QUALIFIED" && job.application_status !== "NEEDS_REVIEW"
      && !job.review_reason && job.match_score > 70 && job.application_url
  );
  const allVisibleSelected = selectableVisibleJobs.length > 0
    && selectableVisibleJobs.every((job) => selectedJobIds.has(job.id));

  useEffect(() => { setCurrentPage((page) => Math.min(page, totalPages)); }, [totalPages]);

  useEffect(() => {
    if (pageSizeChoice !== "AUTO" || !tableViewportRef.current) return undefined;
    const viewport = tableViewportRef.current;
    const calculate = () => {
      const headerHeight = viewport.querySelector("thead")?.getBoundingClientRect().height || 0;
      const rows = [...viewport.querySelectorAll("tbody tr")];
      const rowHeight = rows.length ? Math.max(...rows.map((row) => row.getBoundingClientRect().height)) : 42;
      const next = Math.max(1, Math.floor((viewport.clientHeight - headerHeight - 2) / rowHeight));
      setAutoPageSize((current) => current === next ? current : next);
    };
    const frame = requestAnimationFrame(calculate);
    const observer = new ResizeObserver(calculate);
    observer.observe(viewport);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [pageSizeChoice, jobs, filter, minimumScore, locationFilter]);

  async function runAction(name, path) {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      const options = name === "apply" && selectedJobIds.size
        ? { method: "POST", body: JSON.stringify({ job_ids: [...selectedJobIds] }) }
        : { method: "POST" };
      const result = await api(path, options);
      if (name === "scrape") setMessage(`Scrape complete: ${result.new_jobs} new, ${result.jobs_scored} scored.`);
      else if (name === "score") setMessage(`Scoring complete: ${result.jobs_scored} scored, ${result.qualified} qualified.`);
      else if (name === "contacts") setMessage(`Contact discovery complete: ${result.contacts_saved} saved, ${result.duplicates} already saved, ${result.blocked} blocked, ${result.failed} failed.`);
      else if (name === "outreach") {
        const items = result.results || [result];
        const reason = items.find((item) => item.configuration_error)?.configuration_error
          || (items.some((item) => item.rate_limited) ? "Remaining emails are rate limited; try again later." : "");
        setMessage(`${result.sent} emails sent. ${reason || (result.sent ? "" : "No send completed; check contact, draft inputs, and delivery status.")}`);
      }
      else if (name === "pipeline") setMessage(`${result.dry_run ? "Dry run" : "Automation"} complete: ${result.jobs_discovered} discovered, ${result.jobs_ats_over_70} ATS > 70, ${result.jobs_applied} applied, ${result.emails_sent} emails sent, ${result.linkedin_messages_sent} LinkedIn messages sent, ${result.jobs_skipped} skipped. ${result.errors || 0} stage errors.`);
      else if (result.eligible === 0) {
        setMessage(`None of the ${result.selected} selected jobs are currently eligible.`);
      } else {
        setMessage(`Selected ${result.selected}: ${result.applied} applied, ${result.skipped || 0} skipped, ${result.ineligible || 0} ineligible.`);
        setSelectedJobIds(new Set());
      }
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
      setBusy("");
    }
  }

  async function draftEmail(job) {
    setBusy(`draft-${job.id}`);
    setError("");
    setDraftPreview(null);
    try {
      await api(`/jobs/${job.id}/outreach/generate`, { method: "POST" });
      const draft = await api(`/jobs/${job.id}/outreach`);
      if (draft.status === "DRAFTED") setDraftPreview({ ...draft, company: job.company });
      setMessage(draft.status === "DRAFTED" ? `Personalized draft saved for ${job.company}.` : draft.missing_inputs.join("; "));
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
    } finally { setBusy(""); }
  }

  async function uploadResume(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy("resume");
    setError("");
    setMessage("");
    const body = new FormData();
    body.append("file", file);
    try {
      const uploaded = await api("/resume/upload", { method: "POST", body });
      setResume(uploaded);
      setMessage(`${uploaded.filename} uploaded, parsed, and set active.`);
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
      setBusy("");
    }
  }

  async function connectPlatform(platform) {
    setBusy(`connect-${platform}`);
    setError("");
    setMessage("");
    try {
      const connection = await api(`/connections/${platform}/connect`, { method: "POST" });
      setConnections((current) => [
        ...current.filter((item) => item.platform !== platform),
        connection,
      ]);
      setMessage(`${platform[0].toUpperCase()}${platform.slice(1)} login opened. Complete login manually, then close that browser window.`);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">Local workspace</p>
          <h1>Job Automation</h1>
          <p className="active-resume">Active Resume: <strong>{resume?.filename || "Not uploaded"}</strong>{resume && <span> · Active</span>}</p>
        </div>
        <div className="toolbar" aria-label="Job actions">
          <input ref={fileInputRef} className="file-input" type="file" accept=".pdf,.docx" onChange={uploadResume} />
          <button className="button secondary" onClick={() => fileInputRef.current?.click()} disabled={Boolean(busy)}>{busy === "resume" ? "Uploading..." : "Upload Resume"}</button>
          <button className="button" onClick={() => runAction("scrape", "/scrape?score_jobs=false")} disabled={Boolean(busy)}>Run Scraper</button>
          <button className="button" onClick={() => runAction("score", "/score")} disabled={Boolean(busy)}>Run Scoring</button>
          <button className="button" onClick={() => runAction("contacts", "/contacts/discover?qualified_only=true")} disabled={Boolean(busy)}>Find Contacts</button>
          <button className="button" onClick={() => runAction("apply", "/applications/run")} disabled={Boolean(busy) || !resume}>Auto Apply{selectedJobIds.size ? ` (${selectedJobIds.size})` : ""}</button>
          <button className="button" onClick={() => runAction("outreach", "/dashboard/outreach/send")} disabled={Boolean(busy) || !resume}>Send Outreach</button>
          <button className="button secondary" onClick={() => refresh(filter)} disabled={Boolean(busy)}>Refresh</button>
        </div>
      </header>

      <section className="connections" aria-label="Platform connections">
        <strong>Connections</strong>
        {CONNECTION_PLATFORMS.map((platform) => {
          const connection = connections.find((item) => item.platform === platform);
          const connected = Boolean(connection?.connected);
          const connecting = connection?.status === "connecting";
          const label = `${platform[0].toUpperCase()}${platform.slice(1)}`;
          return <div className="connection" key={platform} title={connection?.message || ""}>
            <span>{label}</span>
            {connected
              ? <span className="connection-state connected">Connected</span>
              : <button className="button secondary" disabled={Boolean(busy) || connecting} onClick={() => connectPlatform(platform)}>
                  {connecting ? "Finish login" : busy === `connect-${platform}` ? "Opening..." : "Connect"}
                </button>}
          </div>;
        })}
      </section>

      <section className="summary" aria-label="Job summary">
        <div><span>Total Jobs</span><strong>{stats.total_jobs}</strong></div>
        <div><span>Qualified</span><strong>{stats.qualified}</strong></div>
        <div><span>Applied</span><strong>{stats.applied}</strong></div>
        <div><span>Contacts Found</span><strong>{stats.contacts_found}</strong></div>
        <div><span>Emails Sent</span><strong>{stats.emails_sent}</strong></div>
        <div><span>LinkedIn Messages Sent</span><strong>{stats.linkedin_messages_sent}</strong></div>
      </section>

      <nav className="filters" aria-label="Filter jobs by status">
        {FILTERS.map((item) => <button key={item} className={filter === item ? "filter active" : "filter"} onClick={() => {
          setFilter(item); setCurrentPage(1); setMessage("");
        }}>{item === "ALL" ? "All" : formatStatus(item)}</button>)}
      </nav>

      <section className="table-filters" aria-label="Filter jobs by score and location">
        <label><span>Minimum score</span><select value={minimumScore} onChange={(event) => {
          setMinimumScore(event.target.value); setCurrentPage(1);
        }}>{SCORE_FILTERS.map((option) => <option key={option.label} value={option.value}>{option.label}</option>)}</select></label>
        <label><span>Location</span><input type="search" value={locationFilter} placeholder="Bengaluru, Remote..." onChange={(event) => {
          setLocationFilter(event.target.value); setCurrentPage(1);
        }} /></label>
        <button className="text-button clear-filters" disabled={!minimumScore && !locationFilter} onClick={() => {
          setMinimumScore(""); setLocationFilter(""); setCurrentPage(1);
        }}>Clear filters</button>
      </section>

      {message && <p className="notice success" role="status">{message}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {busy && <p className="activity" role="status">{busy.startsWith("draft-") ? "Drafting email" : formatStatus(busy)} in progress...</p>}
      {draftPreview && <section className="draft-preview" aria-label="Email draft">
        <button className="text-button" onClick={() => setDraftPreview(null)}>Close draft</button>
        <strong>{draftPreview.email_subject}</strong>
        <p>{draftPreview.email_body}</p>
      </section>}

      <section className="table-card" aria-label="Jobs">
        <div className="table-scroll" ref={tableViewportRef}>
          <table>
            <thead><tr><th className="select-column"><input type="checkbox" aria-label="Select all eligible jobs on this page" checked={allVisibleSelected} disabled={!selectableVisibleJobs.length} onChange={() => {
              setSelectedJobIds((current) => {
                const next = new Set(current);
                selectableVisibleJobs.forEach((job) => allVisibleSelected ? next.delete(job.id) : next.add(job.id));
                return next;
              });
            }} /></th><th>Score</th><th>Company</th><th>Job Title</th><th>Location</th><th>Status</th><th>Recruiter / Contact</th><th>Role</th><th>Email</th><th>LinkedIn</th><th>Email Status</th><th>LinkedIn Status</th><th>Applied Date</th><th>Actions</th></tr></thead>
            <tbody>
              {visibleJobs.map((job) => <tr key={job.id}>
                <td className="select-column"><input type="checkbox" aria-label={`Select ${job.title} at ${job.company}`} checked={selectedJobIds.has(job.id)} disabled={job.status !== "QUALIFIED" || job.application_status === "NEEDS_REVIEW" || Boolean(job.review_reason) || job.match_score == null || job.match_score <= 70 || !job.application_url} onChange={() => setSelectedJobIds((current) => {
                  const next = new Set(current);
                  next.has(job.id) ? next.delete(job.id) : next.add(job.id);
                  return next;
                })} /></td>
                <td className="score" title={job.score_reason || ""}>{job.match_score == null ? "-" : Math.round(job.match_score)}</td>
                <td>{job.company}</td><td className="job-title"><a href={job.application_url} target="_blank" rel="noreferrer">{job.title}</a></td><td>{job.location || "-"}</td>
                <td>{(() => {
                  const displayedStatus = job.application_status === "NEEDS_REVIEW" || job.review_reason
                    ? "NEEDS_REVIEW"
                    : job.status;
                  return <span className={`status status-${displayedStatus.toLowerCase()}`} title={job.review_reason || ""}>{formatStatus(displayedStatus)}</span>;
                })()}</td>
                <td title={job.contact_source || job.discovery_detail || ""}>{job.contact_name || "Not found"}{!job.contact_name && <small className="delivery-note" title={job.discovery_detail || ""}>{job.discovery_status === "BLOCKED" ? "Access blocked" : job.discovery_status === "FAILED" ? "Discovery failed" : job.discovery_status === "COMPLETE" ? "No public contact found" : "Discovery pending"}</small>}</td>
                <td>{job.contact_role || "-"}</td>
                <td>{job.public_work_email || "Not found"}</td>
                <td>{job.linkedin_url ? <a href={job.linkedin_url} target="_blank" rel="noreferrer">Profile</a> : "Not found"}</td>
                <td><span className={`status status-${(job.email_status || "NOT_FOUND").toLowerCase()}`} title={job.email_detail || ""}>{job.email_status || "NOT_FOUND"}</span>{job.email_attempted && job.email_status !== "SENT" && <small className="delivery-note">Attempt recorded; review required</small>}</td>
                <td><span className={`status status-${(job.linkedin_status || "NOT_FOUND").toLowerCase()}`} title={job.linkedin_detail || ""}>{job.linkedin_status || "NOT_FOUND"}</span></td>
                <td>{formatDate(job.applied_at)}</td>
                <td><div className="row-actions">
                  <button className="text-button" disabled={Boolean(busy)} onClick={() => runAction("contacts", `/jobs/${job.id}/contacts/discover`)}>Find Contact</button>
                  <button className="text-button" disabled={Boolean(busy) || !resume || !job.contact_id || !["QUALIFIED", "APPLIED"].includes(job.status)} onClick={() => draftEmail(job)}>Draft Email</button>
                  <button className="text-button" title="Requires a confirmed application and a public work email" disabled={Boolean(busy) || !resume || job.status !== "APPLIED" || !job.application_confirmed || !job.public_work_email || job.email_attempted || !(job.match_score > 70)} onClick={() => runAction("outreach", `/dashboard/jobs/${job.id}/send-email`)}>Send Email</button>
                  {job.linkedin_url ? <a href={job.linkedin_url} target="_blank" rel="noreferrer">Open LinkedIn</a> : <button className="text-button" disabled>Open LinkedIn</button>}
                </div></td>
              </tr>)}
              {!filteredJobs.length && <tr><td className="empty" colSpan="14">No jobs match this filter.</td></tr>}
            </tbody>
          </table>
        </div>
        {filteredJobs.length > 0 && <nav className="pagination" aria-label="Job table pagination">
          <span>Showing {pageStart + 1}-{Math.min(pageStart + pageSize, filteredJobs.length)} of {filteredJobs.length}</span>
          <div className="pagination-controls">
            <label className="page-size">Rows <select value={pageSizeChoice} onChange={(event) => {
              setPageSizeChoice(event.target.value); setCurrentPage(1);
            }}><option value="AUTO">Auto ({autoPageSize})</option>{PAGE_SIZES.map((size) => <option key={size}>{size}</option>)}</select></label>
            <button className="button secondary" onClick={() => setCurrentPage((page) => Math.max(1, page - 1))} disabled={currentPage === 1}>Previous</button>
            <strong>Page {currentPage} of {totalPages}</strong>
            <button className="button secondary" onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))} disabled={currentPage === totalPages}>Next</button>
          </div>
        </nav>}
      </section>
    </main>
  );
}

export default App;
