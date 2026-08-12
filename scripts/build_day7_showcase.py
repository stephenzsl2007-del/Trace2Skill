#!/usr/bin/env python3
"""Build the deterministic, offline Day 7 Trace2Skill showcase."""

from __future__ import annotations

from html import escape
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
OUTPUT_DIR = ROOT / "showcase"
HTML_FILE = OUTPUT_DIR / "trace2skill-demo.html"
MANIFEST_FILE = OUTPUT_DIR / "showcase-manifest.json"
DAY1_ATTESTATION = ROOT / "evidence" / "day1-evidence.json"
SOURCES = (
    ROOT / "evidence" / "day2-trace-summary.json",
    ROOT / "evidence" / "day4-report.json",
    ROOT / "evidence" / "day5-report.json",
    ROOT / "evidence" / "day6" / "eval-pnpm-workspace-logger-baseline.json",
    ROOT / "evidence" / "day6" / "eval-pnpm-workspace-logger-skill-v2.json",
    ROOT / "release-bundles" / "diagnose-javascript-dependency-failures" / "2.0.0" / "release-manifest.json",
)
FORBIDDEN = ("matrix-local.hiclaw.io", "@admin:", "@manager:", "C:\\Users\\", "HICLAW_ADMIN_PASSWORD")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def render_manifest(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def source_attestations() -> list[dict[str, str]]:
    return [{"file": rel(path), "sha256": digest(path.read_bytes())} for path in (DAY1_ATTESTATION, *SOURCES)]


def evidence_model() -> dict[str, Any]:
    day1 = load_json(DAY1_ATTESTATION)
    trace = load_json(SOURCES[0])
    day4 = load_json(SOURCES[1])
    day5 = load_json(SOURCES[2])
    baseline = load_json(SOURCES[3])
    skill_trial = load_json(SOURCES[4])
    release = load_json(SOURCES[5])

    trace_source = trace.get("source", {})
    day1_trace = day1.get("sanitized_trace", {})
    if trace_source.get("sha256") != day1_trace.get("sha256"):
        raise ValueError("Day 2 summary does not match the Day 1 trace attestation")
    raw_trace = ROOT / str(trace_source.get("file", ""))
    if raw_trace.is_file() and digest(raw_trace.read_bytes()) != trace_source.get("sha256"):
        raise ValueError("Local Day 2 trace no longer matches its attested hash")
    if trace.get("run", {}).get("status") != "success" or not trace.get("validation", {}).get("passed"):
        raise ValueError("Day 2 source trace is not successful")
    if day4.get("conclusion") != "no_measured_improvement":
        raise ValueError("Day 4 neutral result changed")
    if day5.get("conclusion") != "refinement_closed_not_independent_transfer":
        raise ValueError("Day 5 refinement boundary changed")
    if baseline.get("validation", {}).get("proposal_rejection") != "agentteams-timeout":
        raise ValueError("Day 6 baseline failure changed")
    if skill_trial.get("result", {}).get("status") != "success" or not skill_trial.get("validation", {}).get("verification_passed"):
        raise ValueError("Day 6 Skill transfer evidence is not successful")
    if release.get("registry", {}).get("external_write_performed") or release.get("registry", {}).get("state") != "staged-local":
        raise ValueError("Release is not in the expected local staging state")
    if skill_trial.get("skill_sha256") != release.get("skill", {}).get("evaluated_skill_sha256"):
        raise ValueError("Day 6 Skill trial and release manifest disagree on evaluated Skill hash")

    return {
        "trace_events": trace["metrics"]["event_count"],
        "trace_tool_calls": trace["metrics"]["tool_call_count"],
        "day4_trials": day4["experiment"]["trial_count"],
        "day4_conclusion": day4["conclusion"],
        "day5_trials": day5["experiment"]["trial_count"],
        "day5_v1": day5["refinement"]["v1_training_status"],
        "day5_v2_initial": day5["refinement"]["v2_initial_heldout_status"],
        "day5_v2_correction": day5["refinement"]["v2_correction_status"],
        "day6_baseline": baseline["result"]["status"],
        "day6_baseline_failure": baseline["validation"]["proposal_rejection"],
        "day6_skill": skill_trial["result"]["status"],
        "day6_verification_ms": skill_trial["validation"]["verification_duration_ms"],
        "skill_version": release["skill"]["version"],
        "skill_status": release["skill"]["status"],
        "validated_scope": release["skill"]["validation_scope"],
        "unvalidated_scope": release["skill"]["unvalidated_scope"],
        "evaluated_hash": release["skill"]["evaluated_skill_sha256"],
        "release_hash": release["skill"]["release_skill_sha256"],
        "package_hash": release["package"]["sha256"],
        "registry_state": release["registry"]["state"],
    }


def badge(label: str, value: str, tone: str = "blue") -> str:
    return f'<span class="badge {tone}"><span>{escape(label)}</span>{escape(value)}</span>'


def build_html(model: dict[str, Any]) -> bytes:
    scope = " · ".join(escape(item) for item in model["validated_scope"])
    unvalidated = "、".join(escape(item) for item in model["unvalidated_scope"])
    evaluated_short = escape(model["evaluated_hash"][:12])
    package_short = escape(model["package_hash"][:12])
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Trace2Skill · Evidence-backed Agent Skills</title>
  <style>
    :root{{--bg:#070b14;--panel:#101827;--panel2:#0c1320;--line:#26344d;--text:#edf4ff;--muted:#98a8c3;--blue:#68a5ff;--cyan:#55e6d2;--amber:#ffc76b;--red:#ff7e91;--radius:20px}}
    *{{box-sizing:border-box}} html,body{{max-width:100%;overflow-x:hidden}} body{{margin:0;background:radial-gradient(circle at 18% 0%,#172746 0,transparent 38%),radial-gradient(circle at 88% 8%,#123534 0,transparent 30%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.55}}
    .wrap{{width:min(1160px,calc(100% - 36px));margin:auto}} header{{padding:28px 0;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ffffff12}} .brand{{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:-.03em}} .mark{{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--blue),var(--cyan));box-shadow:0 0 32px #55e6d244;display:grid;place-items:center;color:#07101b}} .topnote{{color:var(--muted);font-size:13px}}
    .hero{{padding:76px 0 48px;display:grid;grid-template-columns:1.3fr .7fr;gap:42px;align-items:center}} .hero>*{{min-width:0}} .eyebrow{{color:var(--cyan);font-size:13px;text-transform:uppercase;letter-spacing:.16em;font-weight:800;overflow-wrap:anywhere}} h1{{font-size:clamp(44px,6.5vw,82px);line-height:1.02;letter-spacing:-.065em;margin:16px 0 22px;max-width:850px;overflow-wrap:anywhere}} .lead{{font-size:20px;color:#bdc9dc;max-width:720px}} .badges{{display:flex;flex-wrap:wrap;gap:10px;margin-top:28px}} .badge{{border:1px solid #ffffff1b;border-radius:999px;padding:8px 12px;display:flex;gap:8px;font:700 12px ui-monospace,SFMono-Regular,Consolas,monospace;background:#ffffff08}} .badge span{{color:var(--muted);font-family:inherit;font-weight:500}} .badge.green{{border-color:#55e6d255;color:var(--cyan)}} .badge.amber{{border-color:#ffc76b55;color:var(--amber)}}
    .proof{{background:linear-gradient(145deg,#111c30,#0b121f);border:1px solid #ffffff18;border-radius:28px;padding:26px;box-shadow:0 24px 80px #0008}} .proof h2{{margin:0 0 20px;font-size:15px;color:var(--muted);font-weight:600}} .metric{{display:flex;justify-content:space-between;align-items:end;padding:16px 0;border-top:1px solid #ffffff12}} .metric strong{{font-size:34px;line-height:1}} .metric small{{color:var(--muted);max-width:150px;text-align:right}}
    section{{padding:52px 0}} .section-head{{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:24px}} h2{{font-size:32px;letter-spacing:-.04em;margin:0}} .section-head p{{margin:0;color:var(--muted);max-width:540px}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .card{{background:linear-gradient(180deg,#111a2a,#0c131f);border:1px solid #ffffff15;border-radius:var(--radius);padding:22px;min-height:180px}} .card .num{{font:700 12px ui-monospace,monospace;color:var(--blue)}} .card h3{{margin:16px 0 8px;font-size:18px}} .card p{{color:var(--muted);font-size:14px;margin:0}} .arrow{{color:var(--cyan);font-size:22px;margin-top:12px}}
    .loop{{display:grid;grid-template-columns:1fr 78px 1fr;align-items:stretch}} .loop-box{{border:1px solid #ffffff18;border-radius:24px;padding:28px;background:#0d1625}} .loop-box h3{{margin:0 0 18px}} .steps{{display:flex;flex-wrap:wrap;gap:8px}} .step{{background:#ffffff09;border:1px solid #ffffff12;border-radius:10px;padding:9px 11px;font-size:13px}} .connector{{display:grid;place-items:center;color:var(--cyan);font-size:28px}}
    .timeline{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}} .moment{{border-top:3px solid var(--line);padding:18px 4px 0}} .moment.fail{{border-color:var(--amber)}} .moment.pass{{border-color:var(--cyan)}} .moment .day{{font:700 12px ui-monospace,monospace;color:var(--muted)}} .moment h3{{font-size:16px;margin:9px 0}} .moment p{{font-size:13px;color:var(--muted);margin:0}}
    .compare{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .result{{border-radius:24px;padding:26px;border:1px solid #ffffff18;background:#0d1625}} .result.failed{{box-shadow:inset 3px 0 var(--amber)}} .result.success{{box-shadow:inset 3px 0 var(--cyan)}} .status{{font:800 12px ui-monospace,monospace;letter-spacing:.1em}} .failed .status{{color:var(--amber)}} .success .status{{color:var(--cyan)}} .result h3{{font-size:24px;margin:12px 0}} .result p{{color:var(--muted)}}
    .architecture{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}} .layer{{border:1px solid #ffffff18;border-radius:22px;padding:24px;background:#0d1625}} .layer h3{{margin:0 0 6px}} .layer .tag{{color:var(--blue);font:700 11px ui-monospace,monospace}} .layer ul{{padding-left:18px;color:var(--muted);font-size:14px}}
    .scope{{background:linear-gradient(90deg,#172238,#102923);border:1px solid #ffffff1a;border-radius:26px;padding:28px;display:flex;justify-content:space-between;gap:28px}} .scope strong{{display:block;font-size:22px}} .scope p{{margin:8px 0 0;color:var(--muted)}} code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--cyan);word-break:break-all}} footer{{padding:42px 0 60px;color:var(--muted);font-size:13px;border-top:1px solid #ffffff12;display:flex;justify-content:space-between;gap:20px}}
    @media(max-width:850px){{.hero{{grid-template-columns:1fr}}.grid,.timeline,.architecture{{grid-template-columns:1fr 1fr}}.loop{{grid-template-columns:1fr}}.connector{{padding:14px;transform:rotate(90deg)}}}}
    @media(max-width:560px){{.wrap{{width:calc(100% - 24px)}}header{{align-items:flex-start}}.topnote{{display:none}}.grid,.timeline,.architecture,.compare{{grid-template-columns:1fr}}.scope,footer,.section-head{{display:block}}.proof{{margin-top:10px;min-width:0}}h1{{font-size:40px;letter-spacing:-.05em}}.lead{{font-size:17px}}.loop-box{{min-width:0}}}}
  </style>
</head>
<body>
  <header class="wrap"><div class="brand"><span class="mark">↗</span>Trace2Skill</div><div class="topnote">Evidence-backed learning infrastructure for AgentTeams</div></header>
  <main>
    <section class="hero wrap">
      <div><div class="eyebrow">Trace → Skill → Validate → Refine → Reuse</div><h1>把执行过的经验，变成可验证的 Agent 能力。</h1><p class="lead">Trace2Skill 从真实 AgentTeams 执行轨迹中提炼 Skill，在隔离任务上验证，保留失败并迭代，只让有证据的版本进入发布流程。</p><div class="badges">{badge('runtime','AgentTeams v1.1.2')}{badge('skill',model['skill_version'],'green')}{badge('registry',model['registry_state'],'amber')}</div></div>
      <aside class="proof"><h2>当前证据快照</h2><div class="metric"><strong>{model['trace_events']}</strong><small>真实 Trace 事件</small></div><div class="metric"><strong>{model['trace_tool_calls']}</strong><small>可配对工具调用</small></div><div class="metric"><strong>{model['day4_trials'] + model['day5_trials'] + 2}</strong><small>版本化实验轨迹</small></div></aside>
    </section>

    <section class="wrap"><div class="section-head"><div><div class="eyebrow">Two closed loops</div><h2>一次完成任务，长期积累能力</h2></div><p>多数多 Agent 系统只证明任务完成。Trace2Skill 额外验证这次经验能否在新任务上复用。</p></div><div class="loop"><div class="loop-box"><h3>Execution Loop</h3><div class="steps"><span class="step">Manager 拆解</span><span class="step">Worker 调查</span><span class="step">提案执行</span><span class="step">宿主验证</span></div></div><div class="connector">→</div><div class="loop-box"><h3>Learning Loop</h3><div class="steps"><span class="step">Trace 分析</span><span class="step">Candidate Skill</span><span class="step">Held-out</span><span class="step">Refine / Release</span></div></div></div></section>

    <section class="wrap"><div class="section-head"><div><div class="eyebrow">AgentTeams mapping</div><h2>多 Agent 不是装饰</h2></div><p>逻辑角色映射到 Manager 编排与隔离 Worker 上下文；验证权留在确定性宿主，避免 Agent 自证成功。</p></div><div class="grid"><article class="card"><span class="num">01</span><h3>Manager</h3><p>接收任务、建立有限任务状态、传递上下文、提醒并收敛输出契约。</p><div class="arrow">↘</div></article><article class="card"><span class="num">02</span><h3>Investigator</h3><p>从 package、workspace 与错误证据中定位冲突，不读取隐藏答案。</p><div class="arrow">↘</div></article><article class="card"><span class="num">03</span><h3>Executor</h3><p>输出最小 manifest 提案；受保护字段、危险依赖与越权修改由宿主拒绝。</p><div class="arrow">↘</div></article><article class="card"><span class="num">04</span><h3>Verifier</h3><p>在隔离目录中执行离线包管理器，以退出码和原错误消失作为成功证据。</p><div class="arrow">✓</div></article></div></section>

    <section class="wrap"><div class="section-head"><div><div class="eyebrow">Evidence timeline</div><h2>失败也是产品数据</h2></div><p>页面结论直接来自版本化 JSON；不估算 token，不删除失败，不把修正重跑冒充独立迁移。</p></div><div class="timeline"><article class="moment pass"><span class="day">DAY 2</span><h3>真实轨迹</h3><p>{model['trace_events']} events / {model['trace_tool_calls']} tool calls，生命周期校验通过。</p></article><article class="moment"><span class="day">DAY 3</span><h3>候选 Skill</h3><p>从成功路径、失败动作和工具证据提炼，保持 candidate 边界。</p></article><article class="moment"><span class="day">DAY 4</span><h3>中性结果</h3><p>{escape(model['day4_conclusion'])}，没有虚构效率提升。</p></article><article class="moment fail"><span class="day">DAY 5</span><h3>失败驱动修正</h3><p>v1 {model['day5_v1']} → v2 首次 {model['day5_v2_initial']} → correction {model['day5_v2_correction']}。</p></article><article class="moment pass"><span class="day">DAY 6</span><h3>独立迁移</h3><p>新任务首次 Skill 运行通过；版本 {escape(model['skill_version'])} 进入本地发布预检。</p></article></div></section>

    <section class="wrap"><div class="section-head"><div><div class="eyebrow">Fresh held-out result</div><h2>同一任务，两套隔离上下文</h2></div><p>这是一次新的留出任务，不是 Day5 修正任务的重复。</p></div><div class="compare"><article class="result failed"><span class="status">BASELINE · FAILED</span><h3>结果契约超时</h3><p>原始 pnpm 故障已复现，但 Worker 未在 120 秒内返回可验证提案。失败类别：<code>{escape(model['day6_baseline_failure'])}</code></p></article><article class="result success"><span class="status">SKILL v2 · PASSED</span><h3>离线验证成功</h3><p>首次接触新任务即保留受保护元数据并完成最小修复。宿主 pnpm 验证耗时 {model['day6_verification_ms']} ms。</p></article></div></section>

    <section class="wrap"><div class="section-head"><div><div class="eyebrow">Architecture</div><h2>执行、学习、治理三层解耦</h2></div><p>每层通过清晰契约相连，可以替换存储、网关或消息实现，而不改变证据语义。</p></div><div class="architecture"><article class="layer"><span class="tag">RUNTIME</span><h3>AgentTeams + Higress</h3><ul><li>角色与任务编排</li><li>Matrix 上下文和事件</li><li>模型网关、鉴权与路由</li></ul></article><article class="layer"><span class="tag">LEARNING</span><h3>Trace2Skill</h3><ul><li>Trace schema 与审计</li><li>Candidate 生成和版本化</li><li>宿主验证、失败分类和 refinement</li></ul></article><article class="layer"><span class="tag">GOVERNANCE</span><h3>Nacos Skill Registry</h3><ul><li>确定性 ZIP 与哈希</li><li>upload → review → release</li><li>版本、标签、可见性与回滚</li></ul></article></div></section>

    <section class="wrap"><div class="scope"><div><span class="eyebrow">Validated boundary</span><strong>{scope}</strong><p>状态：{escape(model['skill_status'])}。未验证：{unvalidated}。不声称统计显著性或效率提升。</p></div><div><span class="eyebrow">Attestation</span><p>evaluated <code>{evaluated_short}</code><br>package <code>{package_short}</code></p></div></div></section>
  </main>
  <footer class="wrap"><span>Trace2Skill · AgentTeams execution becomes reusable organizational capability.</span><span>Offline showcase · no live model or network dependency</span></footer>
</body>
</html>
"""
    return html.encode("utf-8")


def build_showcase(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    model = evidence_model()
    html = build_html(model)
    text = html.decode("utf-8")
    leaked = [token for token in FORBIDDEN if token in text]
    if leaked:
        raise ValueError(f"Showcase contains local identifier: {leaked[0]}")
    if "https://" in text or "http://" in text:
        raise ValueError("Showcase must not load network resources")
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / HTML_FILE.name
    html_path.write_bytes(html)
    manifest = {
        "schema_version": "0.1.0",
        "artifact": {"file": HTML_FILE.name, "sha256": digest(html), "bytes": len(html), "media_type": "text/html"},
        "sources": source_attestations(),
        "contract": {
            "evidence_derived": True,
            "offline": True,
            "network_dependencies": [],
            "raw_messages_embedded": False,
            "claims": {
                "independent_transfer": "one-new-held-out-task",
                "efficiency_improvement": "not-claimed",
                "statistical_significance": "not-claimed",
                "registry_publication": "staged-local",
            },
        },
    }
    (output_dir / MANIFEST_FILE.name).write_bytes(render_manifest(manifest))
    return manifest


def main() -> int:
    manifest = build_showcase()
    print(f"[PASS] Day 7 offline showcase: {HTML_FILE} sha256={manifest['artifact']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
