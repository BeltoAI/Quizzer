(function(){
  const $=s=>document.querySelector(s);
  const base=()=>($("#baseUrl").value||"").trim()||"https://canvas.instructure.com/";
  const tok =()=>($("#token").value||"").trim();
  const courseId=()=>{ const v=($("#courseSelect").value||"").trim(); return v?parseInt(v,10):null; };
  const setStatus=(el,msg,cls)=>{ el.textContent=msg; el.classList.remove("error","warn"); if(cls) el.classList.add(cls); };

  async function call(path,body){
    const payload = Object.assign({}, body||{}, {canvas_base_url: base(), canvas_token: tok()});
    console.debug("payload", path, payload);
    const r = await fetch(path,{method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    let text = await r.text();
    if(!r.ok){
      try { const j = JSON.parse(text); text = j.detail || text; } catch {}
      throw new Error(text);
    }
    try { return JSON.parse(text); } catch { throw new Error("Bad JSON from server: "+text); }
  }

  function wireTabs(scope){
    const box = document.getElementById(scope+"Box");
    const tabs = box.querySelectorAll(".tabs .tab");
    tabs.forEach(t=>{
      t.addEventListener("click", ()=>{
        tabs.forEach(b=>b.classList.remove("active"));
        t.classList.add("active");
        box.querySelectorAll(".panel").forEach(p=>p.classList.remove("active"));
        $("#"+t.dataset.target).classList.add("active");
      });
    });
  }
  ["quiz","midterm"].forEach(wireTabs);

  function badge(t,cls=""){ const s=document.createElement("span"); s.className="tag "+cls; s.textContent=t; return s; }
  function el(tag, cls="", txt=""){ const e=document.createElement(tag); if(cls) e.className=cls; if(txt!==undefined && txt!==null) e.textContent=txt; return e; }
  function clear(n){ while(n.firstChild) n.removeChild(n.firstChild); }

  function renderPretty(kind, obj){
    const pretty = $("#"+kind+"Pretty");
    clear(pretty);
    const wrap = el("div","pretty-body");
    if(!obj || !obj.title){ wrap.classList.add("empty"); wrap.append(el("div","muted","No data.")); pretty.append(wrap); return; }

    const header = el("div","pretty-header");
    header.append(el("div","title",obj.title || (kind==="quiz"?"Quiz":"Midterm")));
    header.append(badge((obj.questions||[]).length+" questions","soft"));
    wrap.append(header);

    const list = el("div","qlist");
    (obj.questions||[]).forEach((q, idx)=>{
      const card = el("div","qcard");
      const head = el("div","qhead");
      head.append(badge(String(idx+1).padStart(2,"0"),"num"));
      const t = (q.type||"short").toLowerCase();
      head.append(badge(t, t));
      if(typeof q.points==="number") head.append(badge(q.points+" pt","soft"));
      card.append(head);

      const prompt = el("div","qprompt");
      prompt.textContent = String(q.prompt||"").trim();
      card.append(prompt);

      if(t==="mcq" && Array.isArray(q.choices)){
        const ol = el("ol","choices");
        q.choices.forEach((c,i)=>{
          const li = el("li","choice");
          const txt = el("span","choice-text"); txt.textContent = String(c);
          li.append(txt);
          if(Number(q.answer)===i){ li.classList.add("correct"); li.append(badge("✓","ok")); }
          ol.append(li);
        });
        card.append(ol);
      } else if(t==="truefalse"){
        const tf = el("div","tf"); tf.append(el("span","muted","Answer: ")); tf.append(badge(q.answer ? "True" : "False","ok")); card.append(tf);
      } else if(t==="fillblank" && q.answer){
        const fb = el("div","tf"); fb.append(el("span","muted","Blank: ")); fb.append(badge(String(q.answer),"soft")); card.append(fb);
      }
      list.append(card);
    });
    wrap.append(list);
    pretty.append(wrap);

    const countEl = $("#"+kind+"CountBadge");
    if(countEl) countEl.textContent = (obj.questions||[]).length+" items";
  }

  function downloadJSON(filename, obj){
    const blob = new Blob([JSON.stringify(obj,null,2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href=url; a.download=filename; document.body.appendChild(a); a.click();
    setTimeout(()=>{ URL.revokeObjectURL(url); document.body.removeChild(a); }, 0);
  }

  $("#btnAuth").onclick=async()=>{
    const authStatus=$("#authStatus");
    if(!tok()){ setStatus(authStatus,"Paste a Canvas token first.","warn"); return; }
    setStatus(authStatus,"Authenticating…");
    try{
      const d = await call("/auth",{});
      const sel=$("#courseSelect"); sel.innerHTML="";
      (d.courses||[]).forEach(c=>{ const o=document.createElement("option"); o.value=c.id; o.textContent=c.name||("Course "+c.id); sel.appendChild(o); });
      sel.disabled=(d.courses||[]).length===0;
      $("#btnLoadModules").disabled=false; $("#btnGenQuiz").disabled=false; $("#btnGenMidterm").disabled=false;
      setStatus(authStatus,"Authenticated ✓");
    }catch(e){ setStatus(authStatus,"Auth failed: "+e.message,"error"); }
  };

  $("#btnLoadModules").onclick=async()=>{
    const modulesStatus=$("#modulesStatus");
    const cid=courseId(); if(!cid){ setStatus(modulesStatus,"Pick a course.","warn"); return; }
    setStatus(modulesStatus,"Loading modules…");
    try{
      const d = await call("/modules",{course_id:cid});
      window.__modules = d.modules||[];
      renderModules();
      setStatus(modulesStatus,`Loaded ${__modules.length} modules.`);
      $("#warnBox").textContent="";
    }catch(e){ setStatus(modulesStatus,"Modules error: "+e.message,"error"); }
  };

  $("#searchItems").oninput=()=>renderModules();
  $("#btnSelectFiltered").onclick=()=>toggleFiltered(true);
  $("#btnClearFiltered").onclick=()=>toggleFiltered(false);

  function badgeEl(t){const s=document.createElement("span"); s.className="badge"; s.textContent=t; return s;}
  function renderModules(){
    const cont=$("#modulesContainer"); if(!cont) return;
    const ft=($("#searchItems").value||"").toLowerCase();
    cont.innerHTML="";
    (__modules||[]).forEach(m=>{
      const items=(m.items||[]);
      const filtered=items.filter(it=>{const s=(it.type+" "+it.title).toLowerCase(); return !ft||s.includes(ft);});
      const wrap=document.createElement("div"); wrap.className="module"; wrap.dataset.mid=m.id;
      const head=document.createElement("div"); head.className="module-head";
      const mchk=document.createElement("input"); mchk.type="checkbox"; mchk.className="module-check"; mchk.title="Select entire module";
      const caret=document.createElement("span"); caret.className="caret"; caret.textContent="▾";
      const t=document.createElement("span"); t.className="module-title"; t.textContent=`Module #${m.id} — ${m.name}`;
      const c=document.createElement("span"); c.className="counts"; c.textContent=`${filtered.length}/${items.length}`;
      head.append(mchk,caret,t,badgeEl("Items"),c);
      const grid=document.createElement("div"); grid.className="items";
      filtered.forEach(it=>{
        const lab=document.createElement("label"); lab.className="item";
        const chk=document.createElement("input"); chk.type="checkbox"; chk.dataset.mid=m.id;
        if(it.type==="Page") chk.dataset.pageUrl=it.page_url;
        if(it.type==="File") chk.dataset.fileId=it.file_id;
        if(it.type==="Assignment") chk.dataset.assignmentId=it.assignment_id;
        const txt=document.createElement("span"); txt.className="item-text"; txt.textContent=" "+it.title;
        lab.append(chk,badgeEl(it.type),txt); grid.appendChild(lab);
      });
      head.onclick=(ev)=>{ if(ev.target===mchk) return; grid.classList.toggle("hidden"); caret.textContent=grid.classList.contains("hidden")?"▸":"▾"; };
      mchk.onchange=()=>{ grid.querySelectorAll('input[type="checkbox"]').forEach(b=>b.checked=mchk.checked); };
      wrap.append(head,grid); cont.appendChild(wrap);
    });
  }
  window.renderModules = renderModules;

  function toggleFiltered(checked){
    document.querySelectorAll('#modulesContainer label.item').forEach(l=>{
      const style=getComputedStyle(l); if(style.display!=="none" && l.offsetParent!==null){
        const box=l.querySelector('input[type="checkbox"]'); if(box) box.checked=checked;
      }
    });
    document.querySelectorAll('#modulesContainer .module').forEach(mod=>{
      const boxes=mod.querySelectorAll('.items input[type="checkbox"]'); const arr=[...boxes]; const hdr=mod.querySelector('.module-check');
      if(hdr) hdr.checked = arr.length>0 && arr.every(b=>b.checked);
    });
  }

  let lastQuiz=null, lastMid=null;

  async function generate(path,outEl,kind,extra={}){
    const genStatus=$("#genStatus");
    setStatus(genStatus,"Generating…");
    try{
      const cid=courseId(); const sel=collect();
      if(sel.module_ids.length===0 && sel.page_urls.length===0 && sel.file_ids.length===0 && sel.assignment_ids.length===0){
        setStatus($("#warnBox"),"Select a Module (header checkbox) or specific Page/File/Assignment items.","warn"); return;
      }
      const d=await call(path,Object.assign({course_id:cid},sel,extra));
      const key=path.includes("midterm")?"midterm":"quiz"; const obj=d[key];
      outEl.textContent=JSON.stringify(obj,null,2);
      renderPretty(kind, obj);
      const warn = (d.warnings||[]).join(" | ");
      setStatus($("#warnBox"), warn ? warn : "", warn ? "warn" : "");
      setStatus(genStatus,"Done.");
      if(kind==="quiz") { $("#btnPublishQuiz").disabled=false; lastQuiz=obj; $("#quizBox .tabs .tab[data-target='quizPretty']").click(); }
      else { $("#btnPublishMidterm").disabled=false; lastMid=obj; $("#midtermBox .tabs .tab[data-target='midtermPretty']").click(); }
      return obj;
    }catch(e){ setStatus(genStatus,"Error: "+e.message,"error"); console.error(e); }
  }

  function collect(){
    const module_ids=[], page_urls=[], file_ids=[], assignment_ids=[];
    document.querySelectorAll('#modulesContainer .module').forEach(mod=>{
      const mid=parseInt(mod.dataset.mid||"0",10); const hdr=mod.querySelector('.module-check');
      if(mid && hdr && hdr.checked) module_ids.push(mid);
    });
    document.querySelectorAll('input[type="checkbox"][data-mid][data-page-url]').forEach(ch=>{ if(ch.checked) page_urls.push(ch.dataset.pageUrl);});
    document.querySelectorAll('input[type="checkbox"][data-mid]').forEach(ch=>{ if(ch.checked && ch.dataset.fileId){ file_ids.push(parseInt(ch.dataset.fileId,10)); }});
    document.querySelectorAll('input[type="checkbox"][data-mid][data-assignment-id]').forEach(ch=>{ if(ch.checked) assignment_ids.push(parseInt(ch.dataset.assignmentId,10));});
    return {module_ids, page_urls, file_ids, assignment_ids};
  }

  $("#btnGenQuiz").onclick = async()=>{ const cnt=parseInt(($("#quizCount").value||"20"),10); await generate("/generate/quiz",$("#quizOut"),"quiz",{quiz_count:cnt}); };
  $("#btnGenMidterm").onclick = async()=>{ await generate("/generate/midterm",$("#midtermOut"),"midterm"); };

  function pubSettings(){ const s={}; if($("#setPublished").checked) s.published=true; if($("#setShuffle").checked) s.shuffle_answers=true; const tl=parseInt($("#setTimeLimit").value||"0",10); if(tl>0) s.time_limit=tl; const due=($("#setDueAt").value||"").trim(); if(due) s.due_at=due; return s; }

  $("#btnPublishQuiz").onclick=async()=>{
    const st=$("#pubQuizStatus"); st.textContent="Publishing...";
    try{
      if(!lastQuiz) throw new Error("No quiz to publish");
      const tt = prompt("Quiz title:", lastQuiz.title || "Generated Quiz"); if(tt) lastQuiz.title = tt;
      const d=await call("/publish/quiz",{course_id:courseId(), quiz:lastQuiz, settings:pubSettings()});
      const url=d.html_url||"(no url)"; st.innerHTML=`Published ✓ — <a href="${url}" target="_blank">Open in Canvas</a>`;
    }catch(e){ st.textContent="Publish error: "+e.message; st.classList.add("error"); }
  };
  $("#btnPublishMidterm").onclick=async()=>{
    const st=$("#pubMidtermStatus"); st.textContent="Publishing...";
    try{
      if(!lastMid) throw new Error("No midterm to publish");
      const tt = prompt("Midterm title:", lastMid.title || "Generated Midterm"); if(tt) lastMid.title = tt;
      const d=await call("/publish/midterm",{course_id:courseId(), midterm:lastMid, settings:pubSettings()});
      const url=d.html_url||"(no url)"; st.innerHTML=`Published ✓ — <a href="${url}" target="_blank">Open in Canvas</a>`;
    }catch(e){ st.textContent="Publish error: "+e.message; st.classList.add("error"); }
  };

  $("#btnCopyQuiz").onclick = ()=> navigator.clipboard && window.isSecureContext ? navigator.clipboard.writeText($("#quizOut").textContent||"{}") : null;
  $("#btnDownloadQuiz").onclick = ()=> lastQuiz && (function(){ const a=document.createElement("a"); const u=URL.createObjectURL(new Blob([JSON.stringify(lastQuiz,null,2)],{type:"application/json"})); a.href=u; a.download="quiz.json"; a.click(); setTimeout(()=>URL.revokeObjectURL(u),0); })();
  $("#btnCopyMidterm").onclick = ()=> navigator.clipboard && window.isSecureContext ? navigator.clipboard.writeText($("#midtermOut").textContent||"{}") : null;
  $("#btnDownloadMidterm").onclick = ()=> lastMid && (function(){ const a=document.createElement("a"); const u=URL.createObjectURL(new Blob([JSON.stringify(lastMid,null,2)],{type:"application/json"})); a.href=u; a.download="midterm.json"; a.click(); setTimeout(()=>URL.revokeObjectURL(u),0); })();
})();
