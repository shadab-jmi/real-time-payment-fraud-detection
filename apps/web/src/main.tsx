import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import { api, Failure, User, Run, Tx, Case, Page, Summary } from "./api";
import { ModelEvidence, percent } from "./ModelEvidence";
import "./style.css";

const [section = "overview", id] = location.pathname.split("/").filter(Boolean);
const params = new URLSearchParams(location.search);
const money = (n: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(
    n / 100,
  );
const stamp = (s: string) =>
  new Date(s).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const short = (s: string) => s.slice(0, 8);
function href(path: string) {
  const q = new URLSearchParams(params);
  if (path.split("/")[0] !== section) {
    for (const key of ["status", "priority", "action", "cursor"]) q.delete(key);
  }
  return "/" + path + (q.size ? "?" + q : "");
}
function pill(s: string) {
  return <span className={"pill " + s}>{s}</span>;
}
function useFeed<T>(load: () => Promise<T>, key: string) {
  const [data, setData] = useState<T>(),
    [error, setError] = useState(""),
    [time, setTime] = useState(""),
    [rev, setRev] = useState(0),
    [appliedRev,setAppliedRev]=useState(-1);
  useEffect(() => {
    let alive = true,
      timer: number,
      fails = 0,
      busy = false;
    async function poll() {
      clearTimeout(timer);
      if (!alive || document.hidden || busy) return;
      busy = true;
      try {
        const result = await load();
        if (alive) {
          setData(result);
          setAppliedRev(rev);
          setError("");
          setTime(new Date().toLocaleTimeString());
          fails = 0;
        }
      } catch (e) {
        if (alive) {
          setError((e as Error).message);
          fails++;
          if (e instanceof Failure && e.status === 401) location.reload();
        }
      } finally {
        busy = false;
        if (alive && !document.hidden)
          timer = window.setTimeout(poll, Math.min(30000, 2000 * 2 ** fails));
      }
    }
    void poll();
    document.addEventListener("visibilitychange", poll);
    return () => {
      alive = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [key, rev]);
  return { data, error, time, refreshing: rev !== appliedRev, refresh: () => setRev((n) => n + 1) };
}
function Login({ done }: { done: (u: User) => void }) {
  const [error, err] = useState("");
  return (
    <main className="login">
      <Brand />
      <h1>Sign in</h1>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const fields = new FormData(e.currentTarget);
          try {
            done(
              await api<User>("session", "POST", Object.fromEntries(fields)),
            );
          } catch (ex) {
            err((ex as Error).message);
          }
        }}
      >
        <label>
          Username
          <input autoComplete="username" name="username" required autoFocus />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <button>Sign in</button>
        {error && <p role="alert">{error}</p>}
      </form>
      <a className="login-demo" href="/simulation">Try the payment simulator</a>
    </main>
  );
}
function Brand() {
  return (
    <b className="brand">
      <i aria-hidden="true">∿</i> Payment Monitor
    </b>
  );
}
function Filter({
  name,
  title,
  options,
}: {
  name: string;
  title: string;
  options: [string, string][];
}) {
  return (
    <label>
      {title}
      <select
        aria-label={title}
        id={"filter-" + name}
        value={params.get(name) || ""}
        onChange={(e) => {
          const q = new URLSearchParams(params);
          q.delete("cursor");
          if (e.target.value) q.set(name, e.target.value);
          else q.delete(name);
          sessionStorage.setItem("focus", "filter-" + name);
          location.assign("/" + section + "?" + q);
        }}
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}
function filters() {
  const q = new URLSearchParams(params);
  q.delete("range");
  const time = params.get("range");
  if (time)
    q.set("from", new Date(Date.now() - Number(time) * 3600000).toISOString());
  return q;
}
function Chart({ s }: { s: Summary }) {
  const points = s.series.map((p) => ({ t: Date.parse(p.minute), n: p.total }));
  const first = points[0]?.t || Date.now(),
    last = points.at(-1)?.t || first;
  const width = Math.max(1, Math.ceil(((last - first) / 60000 + 1) / 36));
  const bins = Array.from(
    { length: Math.floor((last - first) / 60000 / width) + 1 },
    () => 0,
  );
  points.forEach(
    (p) => (bins[Math.floor((p.t - first) / 60000 / width)] += p.n),
  );
  const max = Math.max(1, ...bins);
  return (
    <section className="panel">
      <h2>Payment volume</h2>
      <p className="sub">
        Synthetic receipts · payments per{" "}
        {width === 1 ? "minute" : width + " minutes"}
      </p>
      <div className="plot">
        <span>{max}</span>
        <div
          className="bars"
          role="img"
          aria-label={"Payment volume, peak " + max}
        >
          {bins.map((v, i) => (
            <i key={i} style={{ height: (100 * v) / max + "%" }} />
          ))}
        </div>
      </div>
      <div className="axis">
        <span>{stamp(new Date(first).toISOString())}</span>
        <span>{stamp(new Date(last).toISOString())}</span>
      </div>
      <details>
        <summary>View chart data</summary>
        <table>
          <thead>
            <tr>
              <th>Minute received</th>
              <th>Payments</th>
            </tr>
          </thead>
          <tbody>
            {s.series.map((p) => (
              <tr key={p.minute}>
                <td>{stamp(p.minute)}</td>
                <td>{p.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
function Queue({ rows }: { rows: Case[] }) {
  return (
    <section className="panel">
      <h2>Open case queue</h2>
      {rows.slice(0, 5).map((c) => (
        <a className="queue" key={c.id} href={href("cases/" + c.id)}>
          <span>
            Case <code>{short(c.id)}</code>
          </span>
          {pill(c.priority)}
        </a>
      ))}
      {!rows.length && <p>No open cases in this scope.</p>}
      <a
        href={
          "/cases?" +
          new URLSearchParams({ ...Object.fromEntries(params), status: "open" })
        }
      >
        View open cases →
      </a>
    </section>
  );
}
function Table({
  tx,
  cases,
  next,
}: {
  tx?: Tx[];
  cases?: Case[];
  next?: string | null;
}) {
  return (
    <section className="panel">
      <h2>{tx ? "Transaction ledger" : "Case queue"}</h2>
      <div className="scroll" tabIndex={0} aria-label="Scrollable results">
        <table>
          <thead>
            <tr>
              {(tx
                ? ["Transaction", "Received", "Amount", "Score", "Action"]
                : ["Case", "Priority", "Status", "Assignee"]
              ).map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tx?.map((t) => (
              <tr key={t.id}>
                <td>
                  <a id={"row-" + t.id} href={href("transactions/" + t.id)}>
                    {short(t.id)}
                  </a>
                </td>
                <td>{stamp(t.received_at)}</td>
                <td>{money(t.amount_minor)}</td>
                <td>{t.score?.toFixed(3) ?? "—"}</td>
                <td>{pill(t.action || t.state)}</td>
              </tr>
            ))}
            {cases?.map((c) => (
              <tr key={c.id}>
                <td>
                  <a id={"row-" + c.id} href={href("cases/" + c.id)}>
                    {short(c.id)}
                  </a>
                </td>
                <td>{pill(c.priority)}</td>
                <td>{pill(c.status)}</td>
                <td>{c.owner || "Unassigned"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!(tx || cases)?.length && <p>No matches for the current filters.</p>}
      <footer>
        {params.has("cursor") && (
          <a
            href={
              "/" +
              section +
              "?" +
              new URLSearchParams([...params].filter(([k]) => k !== "cursor"))
            }
          >
            First page
          </a>
        )}
        {next && (
          <a
            href={
              "/" +
              section +
              "?" +
              new URLSearchParams({
                ...Object.fromEntries(params),
                cursor: next,
              })
            }
          >
            Next page →
          </a>
        )}
      </footer>
    </section>
  );
}
function FullId({ value }: { value: string }) {
  const [copied, set] = useState(false);
  return (
    <div className="full">
      <code>{value}</code>
      <button
        className="secondary"
        onClick={async () => {
          await navigator.clipboard.writeText(value);
          set(true);
        }}
      >
        {copied ? "Copied" : "Copy ID"}
      </button>
    </div>
  );
}
function Evidence({ t }: { t: Tx }) {
  return (
    <section className="panel">
      <h2>Payment evidence</h2>
      <FullId value={t.transaction_id} />
      <dl>
        <dt>Amount</dt>
        <dd>{money(t.amount_minor)}</dd>
        <dt>Received</dt>
        <dd>{stamp(t.received_at)}</dd>
        <dt>Account</dt>
        <dd>{t.account_id}</dd>
        <dt>Decision</dt>
        <dd>{pill(t.action || t.state)}</dd>
      </dl>
      <ModelEvidence t={t} />
      {t.case_id && section !== "simulation" && section !== "cases" && (
        <a href={href("cases/" + t.case_id)}>Open linked case →</a>
      )}
    </section>
  );
}
function CaseDetail({
  c,
  u,
  refresh, refreshing,
}: {
  c: Case;
  u: User;
  refresh: () => void; refreshing: boolean;
}) {
  const [resolution, setResolution] = useState(""),
    [msg, setMsg] = useState(""),
    [busy, setBusy] = useState(false);
  const version = useRef<number | null>(null);
  async function save(kind: string) {
    setBusy(true);
    try {
      await api(
        "cases/" + c.id,
        "PATCH",
        {
          status: kind,
          expected_version: version.current ?? c.version,
          resolution,
        },
        u.csrf,
      );
      setResolution("");
      setMsg("Saved");
      version.current = null;
    } catch (e) {
      setMsg((e as Error).message);
      version.current = null;
    } finally {
      refresh();
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel">
        <h2>Case details</h2>
        <FullId value={c.id} />
        <dl>
          <dt>Status</dt>
          <dd>{pill(c.status)}</dd>
          <dt>Priority</dt>
          <dd>{pill(c.priority)}</dd>
          <dt>Assignee</dt>
          <dd>{c.owner || "Unassigned"}</dd>
        </dl>
        {c.resolution && <p>Resolution · {c.resolution}</p>}
        {u.role === "analyst" && c.status === "open" && (
          <button disabled={busy || refreshing} onClick={() => save("investigating")}>
            Take ownership
          </button>
        )}
        {u.role === "analyst" && c.status === "investigating" && (
          <>
            <label>
              Resolution
              <textarea
                value={resolution}
                onChange={(e) => {
                  version.current ??= c.version;
                  setResolution(e.target.value);
                }}
                maxLength={2000}
              />
            </label>
            <button
              disabled={busy || refreshing || !resolution.trim()}
              onClick={() => save("resolved")}
            >
              Resolve case
            </button>
          </>
        )}
        <p role="status">{msg}</p>
      </section>
    </>
  );
}
// Notes stay mounted across polling and stale updates.
function Notes({ c, u, refresh }: { c: Case; u: User; refresh: () => void }) {
  const [body, set] = useState(""),
    [message, msg] = useState(""),
    [busy, wait] = useState(false);
  const nid = useRef(crypto.randomUUID());
  return (
    <section className="panel">
      <h2>Case notes</h2>
      {c.notes.length ? (
        c.notes.map((n) => (
          <article key={n.id}>
            <b>{n.username}</b> · {stamp(n.created_at)}
            <p>{n.body}</p>
          </article>
        ))
      ) : (
        <p className="sub">No notes yet.</p>
      )}
      {u.role === "analyst" && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            wait(true);
            try {
              await api(
                "cases/" + c.id + "/notes",
                "POST",
                { id: nid.current, body },
                u.csrf,
              );
              set("");
              nid.current = crypto.randomUUID();
              msg("Note saved");
              refresh();
            } catch (ex) {
              msg((ex as Error).message);
            } finally {
              wait(false);
            }
          }}
        >
          <label>
            New note
            <textarea
              maxLength={2000}
              value={body}
              onChange={(e) => set(e.target.value)}
            />
          </label>
          <button disabled={busy || !body.trim()}>Add note</button>
        </form>
      )}
      <p role="status">{message}</p>
      <h3>Timeline</h3>
      {c.timeline.map((e) => (
        <p key={e.id}>
          {stamp(e.occurred_at)} · {e.username || "System"} ·{" "}
          {e.kind.replaceAll("_", " ")}
        </p>
      ))}
      <h3>Linked transactions</h3>
      {c.links.map((l) => (
        <p key={l.transaction_id}>
          <a href={href("transactions/" + l.transaction_id)}>
            {l.transaction_id}
          </a>{" "}
          · {l.relation}
        </p>
      ))}
    </section>
  );
}
type Data = {
  runs: Run[];
  summary?: Summary;
  tx?: Page<Tx>;
  cases?: Page<Case>;
  detail?: Case;
  payment?: Tx;
};
function Workspace({ u, logout }: { u: User; logout: () => void }) {
  const feed = useFeed<Data>(async () => {
    const q = filters();
    const res: Data = { runs: await api("runs") };
    if (section === "overview") {
      res.summary = await api("summary?" + q);
      q.set("status", "open");
      res.cases = await api("cases?" + q);
    }
    if (section === "transactions") {
      res.tx = await api("transactions?" + q);
      if (id) res.payment = await api("transactions/" + id);
    }
    if (section === "cases") {
      res.cases = await api("cases?" + q);
      if (id) {
        res.detail = await api<Case>("cases/" + id);
        res.payment = await api("transactions/" + res.detail.transaction_id);
      }
    }
    return res;
  }, location.href);
  const d = feed.data;
  useEffect(() => {
    const key = sessionStorage.getItem("focus");
    if (d && key) {
      document.getElementById(key)?.focus();
      sessionStorage.removeItem("focus");
    }
  }, [!!d]);
  return (
    <div className="shell">
      <aside>
        <Brand />
        <nav aria-label="Workspace">
          {["overview", "transactions", "cases", "simulation"].map((v, i) => (
            <a
              key={v}
              aria-current={section === v ? "page" : undefined}
              href={"/" + v}
            >
              <b aria-hidden="true">{["▦", "⇄", "▤", "▷"][i]}</b>
              {v}
            </a>
          ))}
        </nav>
        <div className="account">
          <b>{u.username}</b>
          <small>{u.role} access</small>
          <button
            className="secondary"
            onClick={async () => {
              await api("session", "DELETE", undefined, u.csrf);
              logout();
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">Fraud operations</p>
            <h1>
              {section === "overview"
                ? "Overview"
                : section === "cases"
                  ? "Cases"
                  : "Transactions"}
            </h1>
          </div>
          <span className="sub">Updated {feed.time || "—"}</span>
        </header>
        <div className="filters">
          <Filter
            name="run_id"
            title="Simulation run"
            options={[
              ["", "All runs"],
              ...(d?.runs || []).map(
                (r) =>
                  [r.id, r.generator_version + " · " + short(r.id)] as [
                    string,
                    string,
                  ],
              ),
            ]}
          />
          <Filter
            name="range"
            title="Time range"
            options={[
              ["", "All time"],
              ["1", "Last hour"],
              ["24", "Last 24 hours"],
            ]}
          />
          {section === "cases" && (
            <>
              <Filter
                name="status"
                title="Status"
                options={[
                  ["", "All statuses"],
                  ...opts("open", "investigating", "resolved"),
                ]}
              />
              <Filter
                name="priority"
                title="Priority"
                options={[["", "All priorities"], ...opts("normal", "high")]}
              />
            </>
          )}
          {section === "transactions" && (
            <>
              <Filter
                name="action"
                title="Action"
                options={[
                  ["", "All actions"],
                  ...opts("approve", "review", "block"),
                ]}
              />
              <Filter
                name="status"
                title="Status"
                options={[
                  ["", "All statuses"],
                  ...opts("pending", "scored", "failed"),
                ]}
              />
            </>
          )}
        </div>
        {feed.error && (
          <p role="alert">
            {feed.error} <button onClick={feed.refresh}>Retry</button>
          </p>
        )}
        {!d && !feed.error && <p className="skeleton">Loading workspace…</p>}
        {d?.summary && (
          <>
            <div className="metrics">
              {(["accepted", "pending", "reviewed", "blocked"] as const).map(
                (k) => (
                  <section key={k}>
                    <span>{k}</span>
                    <strong>{d.summary![k].toLocaleString()}</strong>
                  </section>
                ),
              )}
            </div>
            <div className="overview">
              <Chart s={d.summary} />
              <Queue rows={d.cases!.items} />
            </div>
          </>
        )}
        {section !== "overview" && d && (
          <div className={id ? "split" : ""}>
            <Table
              tx={d.tx?.items}
              cases={d.cases?.items}
              next={d.tx?.next_cursor || d.cases?.next_cursor}
            />
            {id && (
              <div className="details">
                <a
                  onClick={() => sessionStorage.setItem("focus", "row-" + id)}
                  href={href(section)}
                >
                  ← Back to {section}
                </a>
                {d.detail && (
                  <CaseDetail c={d.detail} u={u} refreshing={feed.refreshing} refresh={feed.refresh} />
                )}
                {d.payment && <Evidence t={d.payment} />}
                {d.detail && (
                  <Notes c={d.detail} u={u} refresh={feed.refresh} />
                )}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
function opts(...v: string[]): [string, string][] {
  return v.map((s) => [s, s]);
}
type SimulationEntry = {id:string; scenario:string; status:string};
type SimulationState = SimulationEntry & {
  started:string; ended:string|null; target:number; rate:number;
  generated:number; processed:number; approved:number; reviewed:number; blocked:number; items:Tx[];
};
function Simulation({user}:{user:User|null}) {
  const [scenario,setScenario]=useState("showcase"), [count,setCount]=useState(20), [rate,setRate]=useState(2);
  const [busy,setBusy]=useState(false), [message,setMessage]=useState("");
  const analyst = user?.role === "analyst";
  const maxRate = analyst ? 200 : 10;
  const maxCount = analyst ? Math.min(720000, Math.max(1,rate) * 3600) : 200;
  const selected=params.get("run_id");
  const feed=useFeed(async()=>{
    const history=await api<SimulationEntry[]>("simulations");
    const run=selected || history[0]?.id;
    const state=run ? await api<SimulationState>("simulations/"+run) : null;
    const payment=id && run ? await api<Tx>("simulations/"+run+"/transactions/"+id) : null;
    return {history,state,payment};
  },location.href);
  const state=feed.data?.state;
  const active=feed.data?.history.find(r=>r.status==="Running");
  async function control(stop:boolean) {
    if (stop && state?.status !== "Running") return;
    setBusy(true); setMessage("");
    try {
      const result=await api<{id:string}>(stop ? "simulations/"+state?.id+"/stop" : "simulations","POST",
        stop ? undefined : {scenario,count,rate});
      location.assign("/simulation?run_id="+result.id);
    } catch(e) {setMessage((e as Error).message); setBusy(false); feed.refresh();}
  }
  return <div className="shell"><aside><Brand/><nav aria-label="Workspace">
    {user && ["overview","transactions","cases"].map(s=><a key={s} href={"/"+s}>{s}</a>)}
    <a aria-current="page" href="/simulation">Simulation</a>
    {!user && <a href="/">Sign in</a>}
  </nav></aside><main><header><div><p className="eyebrow">Fraud operations</p><h1>Simulation</h1></div></header>
    <form className="panel sim-controls" onSubmit={e=>{e.preventDefault();void control(false)}}>
      <label>Scenario<select aria-label="Scenario" aria-describedby="scenario-help" value={scenario} onChange={e=>setScenario(e.target.value)}><option value="mixed">Mixed</option><option value="showcase">Showcase</option></select></label>
      <label>Payments<input type="number" required min={scenario==="showcase"?7:1} max={maxCount} value={count||""} onChange={e=>setCount(Number(e.target.value))}/></label>
      <label>Payments per second<input type="number" required min={1} max={maxRate} value={rate||""} onChange={e=>setRate(Number(e.target.value))}/></label>
      <button disabled={busy || !!active || !feed.data}>Start simulation</button>
      <button type="button" className="secondary" disabled={busy || state?.status !== "Running"} onClick={()=>control(true)}>Stop simulation</button>
      {analyst && <p className="scenario-help sub">Analyst limits · Up to 200 payments/s for one hour. At this rate, choose up to {maxCount.toLocaleString()} payments.</p>}
      <p className="scenario-help sub" id="scenario-help">{scenario==="mixed" ? "Mostly everyday payments, with occasional travel, larger bills, shared devices and bursts of small attempts." : "Starts with six preset payments on one account, then a ₹1,000 web payment to a UK merchant on a new device. Any remaining payments follow the mixed scenario."}</p>
    </form>
    {(message || feed.error) && <p role="alert">{message || feed.error}</p>}
    <div className="filters"><Filter name="run_id" title="Simulation run" options={[["","Latest run"],...(feed.data?.history||[]).map(r=>[r.id,r.scenario+" · "+short(r.id)] as [string,string])]}/></div>
    <section className="panel"><h2>{state?.status || (feed.data?"Idle":"Loading…")}</h2>
    {state && <><p>{state.scenario} · {state.rate} payments/s · {Math.max(0,Math.floor(((state.ended?Date.parse(state.ended):Date.now())-Date.parse(state.started))/1000))} s</p>
    <progress aria-label="Payments processed" value={state.processed} max={state.target}/> {state.processed} / {state.target}</>}</section>
    {feed.data?.payment && <Evidence t={feed.data.payment}/>}
    {state && <><div className="metrics sim-metrics">{(["generated","processed","approved","reviewed","blocked"] as const).map(k=><section key={k}><span>{k}</span><strong>{state[k]}</strong></section>)}</div>
    <section className="panel sim-table"><h2>Live transactions</h2>{state.generated > 200 && <p className="sub">Latest 200 payments · Counters cover the entire run.</p>}<table><thead><tr>{["Transaction ID","Amount","Fraud score","Decision","Received"].map(s=><th key={s}>{s}</th>)}</tr></thead>
    <tbody>{state.items.map(t=><tr key={t.id}><td><a aria-label={t.id} title={t.id} href={"/simulation/"+t.id+"?run_id="+state.id}>{short(t.id)}…{t.id.slice(-4)}</a></td><td>{money(t.amount_minor)}</td><td>{t.score==null?"—":percent(t.score)}</td><td>{pill(t.action||t.state)}</td><td>{stamp(t.received_at)}</td></tr>)}</tbody></table></section></>}
    </main></div>;
}
function App() {
  const [u, set] = useState<User | null | undefined>(undefined);
  useEffect(() => {
    api<User>("me")
      .then(set)
      .catch(() => set(null));
  }, []);
  return u === undefined ? (
    <p>Loading…</p>
  ) : section === "simulation" ? <Simulation user={u}/> : u ? (
    <Workspace u={u} logout={() => set(null)} />
  ) : (
    <Login done={set} />
  );
}
createRoot(document.getElementById("root")!).render(<App />);
