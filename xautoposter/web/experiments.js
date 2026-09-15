'use strict';
let experiments=[], experimentId=null;
const outcomeLabels={...metrics,reactions:'反応合計（4指標）'};
const experimentStatus=e=>!e.publication?'下書き':!e.observations.length?'公開URL登録・未観測':e.review_stale?'新しい観測・再確認が必要':e.reflection?'振り返り保存済み':'反応を観測中';
const localTimeInput=value=>{const d=value?new Date(value):new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const expInput=(key,label,value='',extra='')=>`<label>${label}<input name="${key}" value="${esc(value)}" ${extra}></label>`;
const expArea=(key,label,value='',extra='')=>`<label>${label}<textarea name="${key}" rows="3" ${extra}>${esc(value)}</textarea></label>`;

function draftForm(e) {
  const d=e?.draft||{};
  if(e?.publication)return `<div class="detail-post">${esc(d.text)}</div><dl class="source-meta"><dt>投稿先</dt><dd>@${esc(d.account)}</dd><dt>届けたい価値</dt><dd>${esc(d.goal)}</dd><dt>試す仮説</dt><dd>${esc(d.hypothesis)}</dd><dt>本文の根拠</dt><dd>${esc(d.evidence)||'未記入'}</dd><dt>前回からの変更</dt><dd>${esc(d.change)||'初回'}</dd></dl><p class="scope-box">公開URLを登録した本文と評価条件は固定されています。変更は次の改善案へ残します。</p>`;
  return `<form id="experiment-draft" class="experiment-form"><div class="form-grid">${expInput('account','投稿先のアカウント',d.account,'required maxlength="16" placeholder="@username"')}${expInput('topic','継続して扱うテーマ',d.topic,'required maxlength="100" placeholder="例：セキュリティの優先順位"')}
  <label>投稿の形式<select name="media_type">${[['text','文章のみ'],['image','画像あり'],['video','動画あり']].map(([v,l])=>`<option value="${v}" ${d.media_type===v?'selected':''}>${l}</option>`).join('')}</select></label>
  <label>比較する経過時間<select name="horizon_hours">${[1,24,72].map(h=>`<option value="${h}" ${(d.horizon_hours||24)===h?'selected':''}>投稿後${h}時間</option>`).join('')}</select></label>
  <label class="wide">今回見る指標<select name="metric">${['reply_count','impression_count','like_count','bookmark_count','reactions'].map(k=>`<option value="${k}" ${(d.metric||'reply_count')===k?'selected':''}>${outcomeLabels[k]}</option>`).join('')}</select></label></div>
  ${expArea('goal','誰に、何を持ち帰ってほしいか',d.goal,'required maxlength="1000"')}
  ${expArea('hypothesis','試す仮説（反応が集まると考える理由）',d.hypothesis,'required maxlength="2000"')}
  ${expArea('text','投稿本文',d.text,'required maxlength="10000" placeholder="確認できた事実・経験・意見を区別して書く"')}
  ${expArea('evidence','本文の根拠・確認が必要な点',d.evidence,'maxlength="5000"')}
  ${expArea('change','前回から変える点（初回は空欄で可）',d.change,'maxlength="2000"')}
  <button type="submit" class="button primary">下書きを保存</button><p class="field-note">文章は手動編集です。保存だけではXへ送信されません。文字数・画像の添付・投稿先はXの投稿画面で確認してください。</p></form>`;
}
function publicationPanel(e) {
  if(!e.publication)return `<section class="panel"><div class="panel-head"><h2>02 · Xで公開する</h2>${badge('まだ下書き')}</div><div class="panel-body"><p>保存した本文をXの投稿画面に渡します。Xで公開した後、実際のURLと日時を登録してください。</p><div class="source-buttons"><button class="button" data-exp-action="copy">本文をコピー</button><a class="button primary" href="https://x.com/intent/tweet?text=${esc(encodeURIComponent(e.draft.text))}" target="_blank" rel="noopener noreferrer">保存した本文をXで開く ↗</a></div><p class="field-note">保存済みの本文を渡します。画面を開くだけでは公開状態は変わりません。</p><form id="experiment-publication" class="experiment-form">${expInput('url','公開した投稿のURL','','required type="url" placeholder="https://x.com/username/status/…"')}${expInput('published_at','実際の公開日時（この端末の時刻）','','required type="datetime-local"')}<button type="submit" class="button">公開URLを登録</button></form></div></section>`;
  return `<section class="panel"><div class="panel-head"><h2>02 · 公開の記録</h2>${badge('利用者が登録','blue')}</div><div class="panel-body"><a href="${esc(e.publication.url)}" target="_blank" rel="noopener noreferrer">Xで投稿を開く ↗</a><p>${dt(e.publication.published_at)} · @${esc(e.draft.account)}</p><p class="scope-box">公開URL・本文・日時は利用者の申告です。このアプリがXへの公開成功やアカウントの所有を確認したものではありません。</p></div></section>`;
}
function observationPanel(e) {
  const r=e.report;
  return `<section class="panel"><div class="panel-head"><h2>03 · 反応を観測する</h2>${badge(`${e.observations.length}時点`)}</div><div class="panel-body"><div class="comparison-stat"><small>投稿後${e.draft.horizon_hours}時間の${outcomeLabels[e.draft.metric]}</small><strong>${num(r.value)}</strong><small>${esc(r.reason)}</small></div><p class="small">許容差 ±${r.tolerance_minutes}分。${r.observation?`採用した観測：${dt(r.observation.observed_at)} / 投稿後${num(r.age_minutes,1)}分。`:''}</p><p class="scope-box">${esc(r.conditions)} 比較対象 ${r.comparison_n}件。${esc(r.caution)}</p>${r.parent_difference!=null?`<p>改善元との差：<strong>${r.parent_difference>0?'+':''}${num(r.parent_difference)}</strong>。変更の因果効果を表す値ではありません。</p>`:''}
  ${r.peers.length?`<details><summary>比較の根拠（${r.peers.length}件）</summary>${r.peers.map(p=>`<p><button class="text-button" data-exp-action="select" data-exp-id="${esc(p.id)}">比較投稿を開く</button> ${num(p.value)} / 投稿後${num(p.age_minutes,1)}分 / ${dt(p.observed_at)}</p>`).join('')}</details>`:''}
  <form id="experiment-observation" class="experiment-form"><p class="small">Xで確認した累積値を記録します。空欄は不明です。返信・引用の抜粋には個別URLと確認時刻も残してください。</p>${expInput('observed_at','観測日時（この端末の時刻）',localTimeInput(),'required type="datetime-local"')}<div class="form-grid">${Object.entries(metrics).map(([k,label])=>expInput(k,label,'','type="number" min="0" step="1" placeholder="未取得"')).join('')}</div>${expArea('response_notes','取得できた返信・引用の抜粋、気づき','','maxlength="5000"')}<button type="submit" class="button primary">反応を保存</button></form>
  ${e.observations.length?`<details><summary>観測履歴と根拠</summary><div class="table-scroll"><table><thead><tr><th>観測日時</th>${Object.values(metrics).map(m=>`<th>${m}</th>`).join('')}</tr></thead><tbody>${e.observations.map(s=>`<tr><td>${dt(s.observed_at)}<br>手動観測</td>${Object.keys(metrics).map(k=>`<td>${num(s[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${e.observations.filter(s=>s.response_notes).map(s=>`<div class="evidence-row"><small>${dt(s.observed_at)} · 手動の抜粋</small><p class="preserve-lines">${esc(s.response_notes)}</p></div>`).join('')}</details>`:''}</div></section>`;
}
function reflectionPanel(e) {
  const r=e.reflection||{};
  return `<section class="panel"><div class="panel-head"><h2>04 · 振り返り、次へ</h2>${badge(e.review_stale?'再確認が必要':e.reflection?'保存済み':'未記入',e.review_stale?'orange':'neutral')}</div><div class="panel-body"><p>観測した事実と理由の仮説を分け、次に試す変更を一つ決めます。</p><form id="experiment-reflection" class="experiment-form">${expArea('finding','観測結果から何を学んだか',r.finding,'required maxlength="3000"')}${expArea('evidence','そう考えた根拠（観測日時・指標・返信のURLや抜粋）',r.evidence,'required maxlength="5000"')}${expArea('alternative','別の説明・まだ分からないこと',r.alternative,'required maxlength="3000"')}${expArea('next_change','次の投稿で変える点',r.next_change,'required maxlength="3000"')}<button type="submit" class="button primary">振り返りと根拠を保存</button></form>${e.reflection?`<p class="field-note">${dt(r.saved_at)}時点の${r.observations.length}観測を固定して保存。新しい観測を加えると再確認が必要になります。</p><button class="button" data-exp-action="iterate" ${e.review_stale?'disabled':''}>この振り返りから改善案を作る ↗</button>`:''}</div></section>`;
}
async function renderExperiments() {
  experiments=await json('/experiments');
  const e=experiments.find(e=>e.id===experimentId);
  $('#data-badge').className='badge blue';$('#data-badge').textContent='自分の発信 · 手動記録';
  $('#main').innerHTML=pageHeading('POST. OBSERVE. LEARN.','投稿を、次の学びにつなぐ。','仮説を決めて発信し、反応を記録し、一つ変えてまた試す。',`<button class="button primary" data-exp-action="new">新しい投稿を準備 ＋</button>`)+`<div class="notice info"><div><strong>投稿 → 反応の観測 → 振り返り → 改善した投稿</strong><br>まず数本を同じテーマで試し、届いた質問・経験談も読みます。評価指標は投稿前に決め、観測後の都合に合わせて変えません。</div></div><div class="experiment-layout"><aside class="panel"><div class="panel-head"><h2>自分の投稿</h2>${badge(`${experiments.length}件`)}</div><div class="panel-body experiment-list">${experiments.length?experiments.map(p=>`<button class="experiment-item ${p.id===e?.id?'selected':''}" data-exp-action="select" data-exp-id="${esc(p.id)}">${badge(experimentStatus(p),p.review_stale?'orange':p.reflection?'green':'neutral')}<strong>${esc(p.draft.topic)}</strong><span>${esc(p.draft.text.slice(0,75)||'改善案の本文を記入してください')}</span><small>@${esc(p.draft.account)}${p.parent_id?' · 改善版':''} · ${dt(p.updated_at)}</small></button>`).join(''):'<p class="muted">最初の投稿を右側で準備してください。実際の投稿や反応はまだありません。</p>'}</div></aside><div class="experiment-content">${e?.parent_id?`<div class="notice info"><div>振り返りから作成した改善案です。<button class="text-button" data-exp-action="select" data-exp-id="${esc(e.parent_id)}">改善元の投稿を見る ↗</button><br>変更予定：${esc(e.draft.change)}</div></div>`:''}<section class="panel"><div class="panel-head"><h2>01 · ${e?.publication?'試した本文と仮説':'投稿を準備する'}</h2>${e?badge(experimentStatus(e)):badge('新規')}</div><div class="panel-body">${draftForm(e)}</div></section>${e?publicationPanel(e):''}${e?.publication?observationPanel(e):''}${e?.observations.length?reflectionPanel(e):''}${e?`<section class="panel"><div class="panel-body source-buttons"><button class="button" data-exp-action="export">本文・観測・振り返りをJSON出力</button><button class="button" data-exp-action="history">変更履歴を見る</button></div><div id="experiment-history" class="panel-body" hidden></div></section>`:''}</div></div>`;
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-exp-action]');if(!el||el.disabled)return;
  event.preventDefault();el.disabled=true;
  try{
    const e=experiments.find(e=>e.id===experimentId),action=el.dataset.expAction;
    if(action==='new'){experimentId=null;await renderExperiments();}
    if(action==='select'){experimentId=el.dataset.expId;await renderExperiments();window.scrollTo({top:0});}
    if(action==='copy'){await navigator.clipboard.writeText(e.draft.text);toast('保存した本文をコピーしました');}
    if(action==='iterate'){const next=await json(`/experiments/${e.id}/iterate`,{method:'POST',body:body({version:e.version})});experimentId=next.id;await renderExperiments();toast('改善元の根拠を引き継ぎました。新しい本文を書いて保存してください。');}
    if(action==='export')download(JSON.stringify(e,null,2),`post-learning-${e.id}.json`);
    if(action==='history'){const rows=await json(`/experiments/${e.id}/history`);const box=$('#experiment-history');box.hidden=false;box.innerHTML=rows.map(r=>`<details><summary>v${r.version} · ${dt(r.recorded_at)} · ${esc(r.action)}</summary><pre class="history-json">${esc(JSON.stringify(r.body,null,2))}</pre></details>`).join('');}
  }catch(err){toast(err.message,true);}finally{el.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.id.startsWith('experiment-'))return;
  event.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;
  try{
    const e=experiments.find(e=>e.id===experimentId),fields=Object.fromEntries(new FormData(form));
    let route='/experiments',method='POST';
    if(form.id==='experiment-draft'){fields.horizon_hours=Number(fields.horizon_hours);if(e){route+=`/${e.id}`;method='PUT';fields.version=e.version;}}
    else {route+=`/${e.id}/`;fields.version=e.version;
      if(form.id==='experiment-publication'){route+='publication';fields.published_at=new Date(fields.published_at).toISOString();}
      if(form.id==='experiment-observation'){route+='observations';fields.observed_at=new Date(fields.observed_at).toISOString();Object.keys(metrics).forEach(k=>fields[k]=fields[k]===''?null:Number(fields[k]));}
      if(form.id==='experiment-reflection')route+='reflection';
    }
    const result=await json(route,{method,body:body(fields)});experimentId=result.id;await renderExperiments();toast('発信の記録を保存しました');
  }catch(err){toast(err.message,true);}finally{submit.disabled=false;}
});
