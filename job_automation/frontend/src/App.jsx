import { useCallback, useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const FILTERS = ["ALL", "QUALIFIED", "APPLIED", "INTERVIEW"];
const APPLICATION_METHODS = ["MANUAL", "GREENHOUSE", "LEVER", "WORKDAY", "BROWSER"];
const EMPTY_STATS = {
  total_jobs: 0,
  discovered: 0,
  qualified: 0,
  ready_to_apply: 0,
  applied: 0,
  interview: 0,
};

async function api(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // Keep the status-based message for non-JSON errors.
    }
    throw new Error(message);
  }
  return response.json();
}

function formatStatus(status) {
  return status.replaceAll("_", " ");
}

function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function App() {
  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState(EMPTY_STATS);
  const [filter, setFilter] = useState("ALL");
  const [selectedJob, setSelectedJob] = useState(null);
  const [applicationJob, setApplicationJob] = useState(null);
  const [resumeUsed, setResumeUsed] = useState("");
  const [applicationMethod, setApplicationMethod] = useState("MANUAL");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async (activeFilter = filter) => {
    setBusy("refresh");
    setError("");
    try {
      const query = activeFilter === "ALL" ? "" : `?status=${activeFilter}`;
      const [nextJobs, nextStats] = await Promise.all([
        api(`/jobs${query}`),
        api("/stats"),
      ]);
      setJobs(nextJobs);
      setStats(nextStats);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy("");
    }
  }, [filter]);

  useEffect(() => {
    refresh(filter);
  }, [filter, refresh]);

  async function runAction(name, path) {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      const result = await api(path, { method: "POST" });
      if (name === "scrape") {
        setMessage(`Scrape complete: ${result.new_jobs} new, ${result.duplicates} existing.`);
      } else {
        setMessage(`Scoring complete: ${result.jobs_scored} jobs scored.`);
      }
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
      setBusy("");
    }
  }

  async function updateStatus(job, status) {
    setBusy(`status-${job.id}`);
    setError("");
    try {
      await api(`/jobs/${job.id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setMessage(`${job.title} marked as ${formatStatus(status).toLowerCase()}.`);
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
      setBusy("");
    }
  }

  function openAppliedForm(job) {
    setApplicationJob(job);
    setResumeUsed(job.recommended_resume || "");
    setApplicationMethod("MANUAL");
  }

  async function markApplied(event) {
    event.preventDefault();
    if (!applicationJob) return;
    setBusy(`apply-${applicationJob.id}`);
    setError("");
    try {
      await api(`/jobs/${applicationJob.id}/mark-applied`, {
        method: "POST",
        body: JSON.stringify({
          resume_used: resumeUsed.trim(),
          application_method: applicationMethod,
          applied_at: new Date().toISOString(),
        }),
      });
      setMessage(`${applicationJob.title} marked as applied.`);
      setApplicationJob(null);
      setSelectedJob(null);
      await refresh(filter);
    } catch (requestError) {
      setError(requestError.message);
      setBusy("");
    }
  }

  function rowActions(job) {
    if (job.status === "QUALIFIED") {
      return (
        <>
          <button className="text-button" onClick={() => setSelectedJob(job)}>Open Job</button>
          <button className="text-button" onClick={() => updateStatus(job, "READY_TO_APPLY")} disabled={Boolean(busy)}>Mark Ready</button>
          <button className="text-button danger" onClick={() => updateStatus(job, "SKIPPED")} disabled={Boolean(busy)}>Skip</button>
        </>
      );
    }
    if (job.status === "READY_TO_APPLY") {
      return (
        <>
          <a href={job.application_url} target="_blank" rel="noreferrer">Open Application</a>
          <button className="text-button" onClick={() => openAppliedForm(job)} disabled={Boolean(busy)}>Mark Applied</button>
        </>
      );
    }
    if (job.status === "APPLIED") {
      return <span className="tracked">Application tracked</span>;
    }
    return (
      <>
        <button className="text-button" onClick={() => setSelectedJob(job)}>Open Job</button>
        {job.status !== "SKIPPED" && (
          <button className="text-button danger" onClick={() => updateStatus(job, "SKIPPED")} disabled={Boolean(busy)}>Skip</button>
        )}
      </>
    );
  }

  function selectFilter(nextFilter) {
    setMessage("");
    setFilter(nextFilter);
  }

  return (
    <main className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">Local workspace</p>
          <h1>Job Automation</h1>
        </div>
        <div className="toolbar" aria-label="Job actions">
          <button className="button secondary" onClick={() => refresh(filter)} disabled={Boolean(busy)}>
            Refresh
          </button>
          <button className="button" onClick={() => runAction("scrape", "/scrape")} disabled={Boolean(busy)}>
            {busy === "scrape" ? "Running..." : "Run Scraper"}
          </button>
          <button className="button" onClick={() => runAction("score", "/score")} disabled={Boolean(busy)}>
            {busy === "score" ? "Running..." : "Run Scoring"}
          </button>
        </div>
      </header>

      <section className="summary" aria-label="Job summary">
        <div><span>Total Jobs</span><strong>{stats.total_jobs}</strong></div>
        <div><span>Discovered</span><strong>{stats.discovered}</strong></div>
        <div><span>Qualified</span><strong>{stats.qualified}</strong></div>
        <div><span>Ready</span><strong>{stats.ready_to_apply}</strong></div>
        <div><span>Applied</span><strong>{stats.applied}</strong></div>
        <div><span>Interview</span><strong>{stats.interview}</strong></div>
      </section>

      <nav className="filters" aria-label="Filter jobs by status">
        {FILTERS.map((item) => (
          <button
            key={item}
            className={filter === item ? "filter active" : "filter"}
            aria-pressed={filter === item}
            onClick={() => selectFilter(item)}
          >
            {item === "ALL" ? "All" : formatStatus(item)}
          </button>
        ))}
      </nav>

      {message && <p className="notice success" role="status">{message}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}

      <section className="table-card" aria-label="Jobs">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Score</th>
                <th>Company</th>
                <th>Job Title</th>
                <th>Location</th>
                <th>Source</th>
                <th>Status</th>
                <th>Recommended Resume</th>
                <th>Applied Date</th>
                <th>Resume Used</th>
                <th>Method</th>
                <th>Application Link</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="score">{job.match_score == null ? "-" : Math.round(job.match_score)}</td>
                  <td>{job.company}</td>
                  <td className="job-title">{job.title}</td>
                  <td>{job.location || "-"}</td>
                  <td className="source">{job.source}</td>
                  <td><span className={`status status-${job.status.toLowerCase()}`}>{formatStatus(job.status)}</span></td>
                  <td>{job.recommended_resume || "-"}</td>
                  <td>{formatDate(job.applied_at)}</td>
                  <td>{job.resume_used || "-"}</td>
                  <td>{job.application_method ? formatStatus(job.application_method) : "-"}</td>
                  <td>
                    <a href={job.application_url} target="_blank" rel="noreferrer">Open Application Link</a>
                  </td>
                  <td>
                    <div className="row-actions">
                      {rowActions(job)}
                    </div>
                  </td>
                </tr>
              ))}
              {!jobs.length && !busy && (
                <tr><td className="empty" colSpan="12">No jobs match this filter.</td></tr>
              )}
              {!jobs.length && busy && (
                <tr><td className="empty" colSpan="12">Loading jobs...</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {selectedJob && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setSelectedJob(null)}>
          <section className="job-modal" role="dialog" aria-modal="true" aria-labelledby="job-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div>
                <p className="eyebrow">{selectedJob.company}</p>
                <h2 id="job-dialog-title">{selectedJob.title}</h2>
              </div>
              <button className="close-button" aria-label="Close job details" onClick={() => setSelectedJob(null)}>×</button>
            </div>
            <dl className="job-meta">
              <div><dt>Location</dt><dd>{selectedJob.location || "Not provided"}</dd></div>
              <div><dt>Status</dt><dd>{formatStatus(selectedJob.status)}</dd></div>
              <div><dt>Score</dt><dd>{selectedJob.match_score == null ? "Not scored" : Math.round(selectedJob.match_score)}</dd></div>
            </dl>
            <div className="description">{selectedJob.description || "No description was provided by this source."}</div>
            <div className="modal-actions">
              <a className="button link-button" href={selectedJob.application_url} target="_blank" rel="noreferrer">Open Application Link</a>
            </div>
          </section>
        </div>
      )}

      {applicationJob && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setApplicationJob(null)}>
          <form className="apply-modal" role="dialog" aria-modal="true" aria-labelledby="apply-dialog-title" onSubmit={markApplied} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div>
                <p className="eyebrow">{applicationJob.company}</p>
                <h2 id="apply-dialog-title">Mark as applied</h2>
              </div>
              <button type="button" className="close-button" aria-label="Close application form" onClick={() => setApplicationJob(null)}>×</button>
            </div>
            <label className="form-field">
              <span>Resume used</span>
              <input value={resumeUsed} onChange={(event) => setResumeUsed(event.target.value)} placeholder="resumes/backend.pdf" required />
            </label>
            <label className="form-field">
              <span>Application method</span>
              <select value={applicationMethod} onChange={(event) => setApplicationMethod(event.target.value)}>
                {APPLICATION_METHODS.map((method) => <option key={method} value={method}>{formatStatus(method)}</option>)}
              </select>
            </label>
            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={() => setApplicationJob(null)}>Cancel</button>
              <button type="submit" className="button" disabled={Boolean(busy)}>{busy ? "Saving..." : "Save Application"}</button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}

export default App;
