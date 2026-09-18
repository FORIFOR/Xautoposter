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
  const state=await api("learning");
  document.getElementById("runtime").textContent=`${state.paused?"API収集は停止中":state.token_configured?"APIキー設定済み（接続成功とは別）":"APIキー未設定"} · 最終定期実行 ${state.runtime.last_tick||"まだありません"}`;
  const root=document.getElementById("entries");root.replaceChildren();
  if(!state.entries.length){root.append(el("h2","最初の発信を登録しましょう"),el("p","「投稿の作成・分析・接続設定」→「自分の発信・改善」で下書きと公開URLを登録すると、ここに表示されます。"));return;}
  for(const {entry:e,diagnosis:d} of state.entries){
    const card=el("article"), settings=e.learning||{};
    card.append(el("span",e.publication?"公開URL登録済み":"未公開の検証案","tag"),el("h2",e.draft.topic),el("p",e.draft.text||"本文はまだありません。根拠と検証する変更点をもとに執筆してください。"));
    const facts=el("div",undefined,"facts");d.facts.forEach(f=>facts.append(el("p",f)));card.append(facts,el("p",d.uncertainty,"muted"));
    if(e.parent_evidence)card.append(el("p",`改善元の根拠：${e.parent_evidence.facts.join(" / ")}`));
    if(e.publication){
      const link=el("a","公開投稿を確認");link.href=e.publication.url;link.target="_blank";link.rel="noopener noreferrer";card.append(link);
      const controls=el("div",undefined,"actions"),auto=el("input"),interval=el("input");
      auto.type="checkbox";auto.checked=Boolean(settings.auto_draft);const label=el("label");label.append(auto,document.createTextNode("実測が揃ったら次の検証案を1件作成"));
      interval.type="number";interval.min="1";interval.max="1440";interval.value=String(settings.interval_minutes||15);interval.setAttribute("aria-label","観測間隔（分）");
      controls.append(label,interval,el("span","分ごと"),button(settings.enabled?"定期観測を停止":"定期観測を開始",()=>api(`learning/${e.id}/tracking`,{version:e.version,enabled:!settings.enabled,auto_draft:auto.checked,interval_minutes:Number(interval.value)},"PUT")));
      controls.append(button("保存済みAPI観測と同期",()=>api(`learning/${e.id}/sync`,{version:e.version})));
      if(settings.source_id)controls.append(button("今すぐAPI観測",()=>api(`learning/${e.id}/observe`,{version:e.version})));
      if(d.ready&&!e.next_draft_id)controls.append(button("この実測から次の検証案",()=>api(`learning/${e.id}/propose`,{version:e.version})));
      card.append(controls,el("p",settings.enabled?"定期観測が有効です。既存の予算上限と停止設定が優先されます。":"定期観測は停止中です。開始ボタンを押すまでAPI費用は発生させません。","muted"));
      if(settings.error)card.append(el("p",`観測保留：${settings.error}`));
      if(e.next_draft_id)card.append(el("p","次の検証案を作成済みです。繰り返し実行しても重複作成しません。"));
    }
    const details=el("details"),summary=el("summary","根拠・観測出典を確認"),pre=el("pre",JSON.stringify(d,null,2));details.append(summary,pre);card.append(details);root.append(card);
  }
}
document.getElementById("refresh").addEventListener("click",()=>action(load));
fetch("/api/session").then(r=>r.json()).then(async s=>{token=s.token;await load();}).catch(e=>error(e.message));
