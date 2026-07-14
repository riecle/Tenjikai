#!/usr/bin/env python3
"""Build the Work-mode inline calendar fragment from exported candidate rows."""

from __future__ import annotations

import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / "exports" / "forecast_candidates_365.csv"
TARGET = pathlib.Path(__file__).resolve().parent.parent / "slot-war-room.html"


def load_rows() -> list[dict]:
    compact = []
    with SOURCE.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            compact.append({
                "d": row["date"],
                "id": row["hall_id"],
                "h": row["hall_name"],
                "m": row["market"],
                "r": row["rank"],
                "p": float(row["predicted_mean"]),
                "e": float(row["adjusted_edge"]),
                "u": float(row["utility_edge"]),
                "tm": int(row["travel_minutes"]) if row["travel_minutes"] else None,
                "tp": float(row["travel_penalty"]),
                "c": float(row["confidence"]),
                "n": int(row["sample_n"]) if row["sample_n"] else None,
                "why": row["reason"],
                "risk": json.loads(row["risk_flags"] or "[]"),
                "age": int(row["data_age_days"]),
                "stale": row["stale_warning"] or None,
                "hz": row["horizon_warning"] or None,
            })
    return compact


FRAGMENT = r'''
<div id="slot-atlas-calendar">
  <div class="viz-controls">
    <label class="form-label" for="sa-month">月
      <select class="form-select" id="sa-month"></select>
    </label>
    <label class="form-label" for="sa-market">市場
      <select class="form-select" id="sa-market">
        <option value="all">全登録市場</option>
      </select>
    </label>
  </div>

  <div class="viz-grid sa-stats" aria-live="polite">
    <div class="card viz-stat"><span class="text-muted">S/A/B</span><span class="viz-stat-value" id="sa-playable">0日</span></div>
    <div class="card viz-stat"><span class="text-muted">見送り</span><span class="viz-stat-value" id="sa-skip">0日</span></div>
    <div class="card viz-stat"><span class="text-muted">最多候補</span><span class="viz-stat-value" id="sa-top-hall">—</span></div>
  </div>

  <div class="sa-weekdays text-small text-muted" aria-hidden="true">
    <span>月</span><span>火</span><span>水</span><span>木</span><span>金</span><span>土</span><span>日</span>
  </div>
  <div class="sa-grid" id="sa-grid" aria-label="365日狙い目カレンダー"></div>

  <div class="viz-legend text-small">
    <span><i class="sa-key sa-s"></i>S</span>
    <span><i class="sa-key sa-a"></i>A</span>
    <span><i class="sa-key sa-b"></i>B</span>
    <span><i class="sa-key sa-c"></i>C</span>
    <span><i class="sa-key sa-n"></i>見送り</span>
  </div>

  <div class="card sa-detail" id="sa-detail" aria-live="polite"></div>
  <div class="viz-row sa-actions">
    <button type="button" class="btn btn-primary" id="sa-recheck">この日を最新データで再分析</button>
  </div>
</div>

<style>
#slot-atlas-calendar{color:var(--foreground);display:grid;gap:12px}
#slot-atlas-calendar .sa-stats{grid-template-columns:repeat(3,minmax(0,1fr))}
#slot-atlas-calendar .sa-weekdays,#slot-atlas-calendar .sa-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:5px}
#slot-atlas-calendar .sa-weekdays span{text-align:center}
#slot-atlas-calendar .sa-empty{min-height:42px}
#slot-atlas-calendar .sa-day{min-height:48px;justify-content:center;padding:6px}
#slot-atlas-calendar .sa-day[data-rank="S"]{background:color-mix(in srgb,var(--viz-series-1) 22%,var(--card));color:var(--card-foreground)}
#slot-atlas-calendar .sa-day[data-rank="A"]{background:color-mix(in srgb,var(--viz-series-2) 20%,var(--card));color:var(--card-foreground)}
#slot-atlas-calendar .sa-day[data-rank="B"]{background:color-mix(in srgb,var(--viz-series-3) 18%,var(--card));color:var(--card-foreground)}
#slot-atlas-calendar .sa-day[data-rank="C"]{background:var(--muted);color:var(--muted-foreground)}
#slot-atlas-calendar .sa-day[data-rank="NO BET"]{background:transparent;color:var(--muted-foreground)}
#slot-atlas-calendar .sa-day[aria-pressed="true"]{box-shadow:0 0 0 2px var(--ring) inset}
#slot-atlas-calendar .viz-legend{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
#slot-atlas-calendar .viz-legend span{display:inline-flex;align-items:center;gap:4px}
#slot-atlas-calendar .sa-key{width:12px;height:12px;border:1px solid var(--border);border-radius:3px;display:inline-block}
#slot-atlas-calendar .sa-s{background:color-mix(in srgb,var(--viz-series-1) 22%,var(--card))}
#slot-atlas-calendar .sa-a{background:color-mix(in srgb,var(--viz-series-2) 20%,var(--card))}
#slot-atlas-calendar .sa-b{background:color-mix(in srgb,var(--viz-series-3) 18%,var(--card))}
#slot-atlas-calendar .sa-c{background:var(--muted)}
#slot-atlas-calendar .sa-n{background:transparent}
#slot-atlas-calendar .sa-detail{display:grid;gap:5px}
#slot-atlas-calendar .sa-detail-line{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
#slot-atlas-calendar .sa-actions{justify-content:flex-end}
@media(max-width:480px){#slot-atlas-calendar .sa-day{min-height:42px;padding:4px}#slot-atlas-calendar .sa-stats{grid-template-columns:1fr 1fr}#slot-atlas-calendar .sa-stats .card:last-child{grid-column:1/-1}}
</style>

<script>
(function(){
  "use strict";
  const root=document.getElementById("slot-atlas-calendar");
  const rows=__DATA__;
  const rankValue={"NO BET":0,"C":1,"B":2,"A":3,"S":4};
  const monthSelect=root.querySelector("#sa-month");
  const marketSelect=root.querySelector("#sa-market");
  const grid=root.querySelector("#sa-grid");
  const detail=root.querySelector("#sa-detail");
  const byDate=new Map();
  rows.forEach(function(row){if(!byDate.has(row.d))byDate.set(row.d,[]);byDate.get(row.d).push(row)});
  const months=Array.from(new Set(rows.map(function(row){return row.d.slice(0,7)}))).sort();
  const markets=Array.from(new Set(rows.map(function(row){return row.m}))).sort();
  markets.forEach(function(market){const o=document.createElement("option");o.value=market;o.textContent=market;marketSelect.appendChild(o)});
  months.forEach(function(month){const o=document.createElement("option");o.value=month;o.textContent=month.replace("-","年")+"月";monthSelect.appendChild(o)});
  monthSelect.value=months[0];
  let selectedDate=rows[0].d;

  function candidates(date){
    const market=marketSelect.value;
    return (byDate.get(date)||[]).filter(function(row){return market==="all"||row.m===market});
  }
  function bestFor(date){
    const list=candidates(date).slice().sort(function(a,b){return rankValue[b.r]-rankValue[a.r]||b.u-a.u||b.c-a.c});
    return list[0];
  }
  function labelFor(row){return row.r==="NO BET"||row.r==="C"?"見送り（相対首位: "+row.h+"）":row.h}
  function daysInMonth(month){const bits=month.split("-").map(Number);return new Date(bits[0],bits[1],0).getDate()}
  function iso(month,day){return month+"-"+String(day).padStart(2,"0")}
  function renderDetail(){
    const row=bestFor(selectedDate);
    if(!row){detail.textContent="対象期間外";return}
    const n=row.n===null?"n未記載":"n="+row.n;
    const risks=row.risk.length?" / リスク: "+row.risk.join("・"):"";
    const warnings=[row.stale,row.hz].filter(Boolean);
    detail.innerHTML="";
    const line1=document.createElement("div");line1.className="sa-detail-line";
    const badge=document.createElement("span");badge.className="viz-badge";badge.textContent=row.r;
    const strong=document.createElement("strong");strong.textContent=selectedDate+"  "+labelFor(row);
    line1.appendChild(badge);line1.appendChild(strong);
    const travel=row.tm===null?"移動未設定":"尾山台から約"+row.tm+"分・調整-"+row.tp;
    const line2=document.createElement("div");line2.className="text-small";line2.textContent=row.why+" / 予測平均 "+(row.p>=0?"+":"")+row.p+"枚 / 判定余白 "+(row.e>=0?"+":"")+row.e+" / "+travel+" / 効用 "+(row.u>=0?"+":"")+row.u+" / "+n+" / 信頼度 "+Math.round(row.c*100)+"%"+risks;
    detail.appendChild(line1);detail.appendChild(line2);
    if(warnings.length){const warn=document.createElement("div");warn.className="text-small text-muted";warn.textContent=warnings.join(" / ");detail.appendChild(warn)}
  }
  function render(){
    const month=monthSelect.value;
    const parts=month.split("-").map(Number);
    const first=new Date(parts[0],parts[1]-1,1);
    const leading=(first.getDay()+6)%7;
    grid.innerHTML="";
    for(let i=0;i<leading;i++){const empty=document.createElement("div");empty.className="sa-empty";grid.appendChild(empty)}
    const counts=new Map();let playable=0;let skip=0;
    for(let day=1;day<=daysInMonth(month);day++){
      const date=iso(month,day);const row=bestFor(date);
      const button=document.createElement("button");button.type="button";button.className="btn viz-tile sa-day";button.textContent=String(day);
      if(row){button.dataset.rank=row.r;button.setAttribute("aria-label",date+" "+row.r+" "+labelFor(row));if(rankValue[row.r]>=2){playable++;counts.set(row.h,(counts.get(row.h)||0)+1)}else{skip++}}else{button.disabled=true;button.setAttribute("aria-label",date+" 対象期間外")}
      button.setAttribute("aria-pressed",date===selectedDate?"true":"false");
      button.addEventListener("click",function(){selectedDate=date;render()});grid.appendChild(button);
    }
    root.querySelector("#sa-playable").textContent=playable+"日";
    root.querySelector("#sa-skip").textContent=skip+"日";
    const top=Array.from(counts.entries()).sort(function(a,b){return b[1]-a[1]})[0];
    root.querySelector("#sa-top-hall").textContent=top?top[0].replace("エスパス日拓","").replace("楽園",""):"—";
    renderDetail();
  }
  monthSelect.addEventListener("change",function(){selectedDate=monthSelect.value+"-01";render()});
  marketSelect.addEventListener("change",render);
  root.querySelector("#sa-recheck").addEventListener("click",async function(){
    const row=bestFor(selectedDate);if(!row||!window.openai||!window.openai.sendFollowUpMessage)return;
    await window.openai.sendFollowUpMessage({title:"最新データで再分析",prompt:selectedDate+"のパチスロ狙い目を、全登録市場（渋谷・東横線・池上線・蒲田・京急線・大井町線・溝の口・副都心線北上・横浜・鶴見・川崎・センター南）の公開最新データ、来店・取材、前日実測まで更新して再分析してください。現在のカレンダー候補は「"+labelFor(row)+"」、根拠は「"+row.why+"」です。主張→予測→判定条件の形で、行く店・機種・位置・見送り条件を出してください。"});
  });
  render();
})();
</script>
'''


def main() -> None:
    payload = json.dumps(load_rows(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    TARGET.write_text(FRAGMENT.replace("__DATA__", payload).strip() + "\n", encoding="utf-8")
    print(TARGET)


if __name__ == "__main__":
    main()
