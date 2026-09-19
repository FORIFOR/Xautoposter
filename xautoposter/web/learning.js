"use strict";
let token = "", working = false;
const el = (tag, text, cls) => { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; };
async function api(path, body, method="POST") {
  const response=await fetch(`/api/${path}`, {method:body===undefined?"GET":method, headers:{"X-Local-Token":token,...(body===undefined?{}:{"Content-Type":"application/json"})},body:body===undefined?undefined:JSON.stringify(body)});
  const value=await response.json(); if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:"処理に失敗しました。再表示して入力を確認してください"); return value;
}
function error(value){const n=document.getElementById("error");n.textContent=value;n.hidden=!value;}
async function action(fn){if(working)return;working=true;document.querySelectorAll("button").forEach(b=>b.disabled=true);error("");try{await fn();await load();}catch(e){error(e.message);}finally{working=false;document.querySelectorAll("button").forEach(b=>b.disabled=false);}}
function button(text,fn){const n=el("button",text);n.type="button";n.addEventListener("click",()=>action(fn));return n;}
async function load(){
  const [state, writing]=await Promise.all([api("learning"),api("authoring")]);
  document.getElementById("runtime").textContent=state.paused?"反応の自動チェックは停止中":state.token_configured?"反応をチェックする準備ができています":"初回の接続設定が必要です";
  const root=document.getElementById("entries");root.replaceChildren();
  if(!state.entries.length){root.append(el("h2","最初の発信を登録しましょう"),el("p","「投稿の作成・分析・接続設定」→「自分の発信・改善」で下書きと公開URLを登録すると、ここに表示されます。"));return;}
  for(const {entry:e,diagnosis:d} of state.entries){
    const card=el("article"), settings=e.learning||{};
    card.append(el("span",e.publication?"公開した投稿":"次の投稿案","tag"),el("h2",e.draft.topic),el("p",e.draft.text||"AIに次の投稿文を提案してもらえます。"));
    const facts=el("div",undefined,"facts");d.facts.forEach(f=>facts.append(el("p",f)));const insight=el("details");insight.append(el("summary","分析結果を見る"),facts,el("p",d.uncertainty,"muted"));card.append(insight);
    if(e.parent_evidence){const p=el("details");p.append(el("summary","前の投稿から引き継いだ学び"),el("p",e.parent_evidence.facts.join(" / ")));card.append(p);}
    if(e.publication){
      const link=el("a","公開投稿を確認");link.href=e.publication.url;link.target="_blank";link.rel="noopener noreferrer";card.append(link);
      const controls=el("div",undefined,"actions"),auto=el("input"),interval=el("input");
      auto.type="checkbox";auto.checked=Boolean(settings.auto_draft);const label=el("label");label.append(auto,document.createTextNode("反応が集まったら次の投稿案を自動で作る"));
      interval.type="number";interval.min="1";interval.max="1440";interval.value=String(settings.interval_minutes||15);interval.setAttribute("aria-label","観測間隔（分）");
      controls.append(label,button(settings.enabled?"反応チェックを停止":"反応を自動チェック",()=>api(`learning/${e.id}/tracking`,{version:e.version,enabled:!settings.enabled,auto_draft:auto.checked,interval_minutes:Number(interval.value)},"PUT")));const advanced=el("details");const advSummary=el("summary","詳細設定");const adv=el("div",undefined,"actions");adv.append(interval,el("span","分ごとに確認"),button("保存済みデータと同期",()=>api(`learning/${e.id}/sync`,{version:e.version})));if(settings.source_id)adv.append(button("今すぐ確認",()=>api(`learning/${e.id}/observe`,{version:e.version})));advanced.append(advSummary,adv);controls.append(advanced);
      if(d.ready&&!e.next_draft_id)controls.append(button("次の投稿案を作る",()=>api(`learning/${e.id}/propose`,{version:e.version})));
      card.append(controls,el("p",settings.enabled?"定期観測が有効です。既存の予算上限と停止設定が優先されます。":"定期観測は停止中です。開始ボタンを押すまでAPI費用は発生させません。","muted"));
      if(settings.error)card.append(el("p",`観測保留：${settings.error}`));
      if(e.next_draft_id)card.append(el("p","次の検証案を作成済みです。繰り返し実行しても重複作成しません。"));
    }
    if(!e.publication){
      const area=el("section"), consent=el("input"), label=el("label");consent.type="checkbox";
      label.append(consent,document.createTextNode("AIで投稿文を作る（外部AIを利用するため料金が発生する場合があります）"));
      area.append(el("h3","次の投稿文を作る"),el("p","これまでの反応を参考に、AIが投稿文を1つ提案します。","muted"),label);const tech=el("details");tech.append(el("summary","AIの詳細"),el("p",`モデル：${writing.model||"未設定"} · 本日の生成 ${writing.requests_today_utc}/${writing.daily_request_limit}`,"muted"));area.append(tech);
      area.append(button("投稿文を作る",async()=>{if(!consent.checked)throw new Error("外部送信と費用の許可を確認してください");await api(`authoring/${e.id}/generate`,{version:e.version,consent_external_processing:true,consent_api_cost:true});}));
      const run=writing.runs.find(r=>r.entry_id===e.id && r.source_version===e.version);
      if(run){
        area.append(el("p",`生成状態：${run.state}`));
        if(run.state==="ready"){
          area.append(el("p",run.payload.text,"suggestion"),el("p",run.payload.summary,"muted"));
          const why=el("details");why.append(el("summary","なぜこの投稿案？"));for(const item of run.payload.interpretations)why.append(el("p",`${item.interpretation} / 参考「${item.quote}」 / 別の見方：${item.alternative}`));area.append(why);
          for(const caution of run.payload.cautions)area.append(el("p",caution,"muted"));
          area.append(button("この投稿案を使う",()=>api(`authoring/${e.id}/apply`,{version:e.version,run_id:run.id,confirmed:true})));
        }else if(run.payload.error)area.append(el("p",run.payload.error));
      }
      if(e.authoring)area.append(el("p","LLM原稿を採用済みです。事実・文字数・投稿先は公開前にX上で最終確認してください。","muted"));
      card.append(area);
    }
    const details=el("details"),summary=el("summary","根拠・観測出典を確認"),pre=el("pre",JSON.stringify(d,null,2));details.append(summary,pre);card.append(details);root.append(card);
  }
}
document.getElementById("refresh").addEventListener("click",()=>action(load));
fetch("/api/session").then(r=>r.json()).then(async s=>{token=s.token;await load();}).catch(e=>error(e.message));
