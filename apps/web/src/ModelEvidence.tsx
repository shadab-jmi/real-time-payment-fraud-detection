import React from "react";
import { Tx } from "./api";

const features: Record<string, [string, string]> = {
  log_amount: ["Payment amount", "The model uses the natural logarithm of one plus the amount in rupees."],
  cross_border: ["Cross-border payment", "1 when the account and merchant countries differ, otherwise 0."],
  channel_web: ["Website payment", "1 when the payment used the web channel, otherwise 0."],
  channel_app: ["App payment", "1 when the payment used the app channel, otherwise 0."],
  channel_pos: ["Point-of-sale payment", "1 when the payment used a point-of-sale terminal, otherwise 0."],
  hour_sin: ["Time of day · sine", "Sine of 2π × UTC event hour / 24. This coordinate peaks at 06:00 and reaches its minimum at 18:00. Together with cosine it represents the daily cycle."],
  hour_cos: ["Time of day · cosine", "Cosine of 2π × UTC event hour / 24. This coordinate peaks at midnight and reaches its minimum at noon. Together with sine it represents the daily cycle."],
  device_missing: ["Missing device ID", "1 when the payment has no device ID, otherwise 0."],
  prior_count_10m: ["Recent payment attempts", "Number of earlier accepted payments on this account within the preceding 10 minutes."],
  prior_spend_10m: ["Recent payment total", "Total amount in rupees across earlier accepted payments on this account within the preceding 10 minutes."],
  distinct_devices_24h: ["Devices seen in 24 hours", "Number of distinct known devices on earlier payments for this account within the preceding 24 hours."],
  new_device: ["New device for account", "1 when the current device ID is present and was absent from this account’s preceding 24 hours of payments. A missing ID gives 0."],
};

export const percent = (value: number) =>
  value.toLocaleString("en-US", {style: "percent", maximumFractionDigits: 2});
const signed = (value: number) => (value > 0 ? "+" : "") + String(value);

export function ModelEvidence({t}: {t: Tx}) {
  if (t.score == null) return null;
  const rows = t.contributions ?? [];
  const ordered = [...rows].sort((a,b) => Math.abs(b.signed_value) - Math.abs(a.signed_value));
  const maximum = Math.abs(ordered[0]?.signed_value ?? 0);
  return <div className="model-evidence">
    <div className="score-heading"><div><h3>Fraud score</h3><strong>{percent(t.score)}</strong></div>
      <span className={"pill "+t.action}>{t.action}</span></div>
    <div className="risk-track" aria-hidden="true">
      <i style={{width:100*t.review_threshold+"%"}}/>
      <i style={{width:100*(t.block_threshold-t.review_threshold)+"%"}}/><i/>
      <b style={{left:100*t.score+"%"}}/>
    </div>
    <div className="thresholds"><span>Approve below {percent(t.review_threshold)}</span>
      <span>Review ≥ {percent(t.review_threshold)}</span><span>Block ≥ {percent(t.block_threshold)}</span></div>
    <h3>Model explanation</h3>
    <p className="sub">Each bar shows how a feature shifts the model’s fraud prediction from its baseline, measured in log-odds.</p>
    <div className="directions"><span>← Decreases risk</span><span>Increases risk →</span></div>
    {ordered.map(v => {
      const [label,description] = features[v.feature_name] ?? [v.feature_name,""];
      const direction = v.signed_value > 0 ? "Increases risk" : v.signed_value < 0 ? "Decreases risk" : "No shift";
      const unit = t.manifest?.find(f=>f.name===v.feature_name)?.unit;
      return <div className="factor" key={v.feature_name} tabIndex={0} aria-describedby={"tip-"+v.feature_name}>
        <span>{label}</span>
        <span className="scale" aria-hidden="true"><i style={{width:(maximum ? 50*Math.abs(v.signed_value)/maximum : 0)+"%",
          ...(v.signed_value<0 ? {right:"50%",background:"#3469a0"} : {left:"50%"})}}/></span>
        <code>{v.signed_value>0?"+":""}{v.signed_value.toFixed(3)}</code>
        <span className="feature-tip" role="tooltip" id={"tip-"+v.feature_name}>{description}<br/>
          Feature value: {v.feature_value} {unit}. {direction}: {signed(v.signed_value)} log-odds.</span>
      </div>;
    })}
    <details className="technical"><summary>Technical details</summary>
      <dl>{[["Model version",t.model_version],["Raw fraud score",t.score],["Review threshold",t.review_threshold],
        ["Block threshold",t.block_threshold],["Bias (log-odds)",t.bias],["Raw margin (log-odds)",t.raw_margin]].map(([label,value])=>
          value!=null && <React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>)}</dl>
      <div className="technical-table"><table><thead><tr><th>Feature</th><th>Model input</th><th>SHAP (log-odds)</th></tr></thead>
        <tbody>{rows.map(v=><tr key={v.feature_name}><td>{v.feature_name}</td><td>{v.feature_value}</td><td>{signed(v.signed_value)}</td></tr>)}</tbody></table></div>
    </details>
  </div>;
}
