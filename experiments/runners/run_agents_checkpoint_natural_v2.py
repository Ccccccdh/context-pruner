"""Frozen same-history next-action experiment through the real Agents SDK.

prepare: MockTransport-only SDK capture + optional local quality gates.
run: one irreversible, bounded provider batch; no resume or task replacement.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

import httpx
from agents import Agent, Runner, RunConfig
from agents.models.chatcmpl_converter import Converter

from .agents_provider_audit_v2 import RequestLedger, AuditedTransport, request_scope, make_sdk_model, sdk_settings, sha, canonical, MODEL, PRICES
from .agents_evidence_review_v1 import load_tooling

ROOT=Path(__file__).resolve().parents[2]
REPLAY=ROOT/"runs/agents-evidence-lifecycle-v1-20261014/checkpoint-replay"
ARMS=("none","k1_guarded","k0_guarded")
REPAIR="Your last reply could not be parsed. Reply with exactly one JSON object and no other text: either {\"read\": {\"path\": \"...\", \"first\": 1, \"last\": 40}} or {\"patch\": \"<unified diff>\"}."
SOURCE_FILES=("experiments/runners/agents_provider_audit_v2.py","experiments/runners/run_agents_checkpoint_natural_v2.py",
    "experiments/runners/agents_evidence_review_v1.py","context_pruner/adapters/openai_agents_lifecycle_v1.py",
    ".tooling/agents_k_axis_filter.py",".tooling/agents_repair_runner.py",".tooling/agents_w2_real_quality_endpoint.py")


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_checkpoints(replay):
    replay=Path(replay)
    rows=json.loads((replay/"checkpoints.json").read_text(encoding="utf-8"))
    manifest=json.loads((replay/"input_manifest.json").read_text(encoding="utf-8"))
    counts,selected,seen={},[],set()
    for row in rows:
        if not row["matched"] or counts.get(row["task"],0)>=2:
            continue
        if not any(row["arms"][a]["digest"]!=row["arms"]["none"]["digest"] for a in ARMS[1:]):
            continue
        p=replay/row["comparison_file"]
        if p.parent.resolve()!=replay.resolve() or file_sha(p)!=manifest.get(p.name):
            raise ValueError("checkpoint snapshot manifest mismatch")
        snapshot=json.loads(p.read_text(encoding="utf-8"))
        filt=load_tooling("agents_k_axis_filter")
        if snapshot["input_digest"] != row["recorded_digest"] or filt.message_digest(snapshot["arms"]["none"]["messages"])!=row["recorded_digest"]:
            raise ValueError("historical H digest mismatch")
        if (row["task"],row["recorded_digest"]) in seen:
            continue
        for arm in ARMS:
            if filt.message_digest(snapshot["arms"][arm]["messages"]) != row["arms"][arm]["digest"]:
                raise ValueError("derived arm digest mismatch")
        seen.add((row["task"],row["recorded_digest"]))
        counts[row["task"]]=counts.get(row["task"],0)+1
        selected.append({"id":row["id"],"task":row["task"],"recorded_digest":row["recorded_digest"],
                         "snapshot":snapshot})
    return selected


def build_plan(selected):
    rows=[]
    for checkpoint in selected:
        for repeat in range(1,4):
            order=ARMS[repeat%3:]+ARMS[:repeat%3]
            for arm in order:
                rows.append({"sample_id":f"{checkpoint['id']}-{arm}-r{repeat}","checkpoint_id":checkpoint["id"],
                             "task":checkpoint["task"],"arm":arm,"repeat":repeat})
    return rows


def parse_action(text):
    old=load_tooling("agents_repair_runner")
    try:
        objects=[o for o in old.iter_json_objects(old.strip_reasoning(text or "")) if isinstance(o,dict)]
        patches=[o for o in objects if "patch" in o]
        if patches and not isinstance(patches[-1]["patch"],str):
            return {"kind":"invalid","reason":"patch must be a string"}
        action=old.parse_model_turn(text)
        if action["kind"]=="read":
            path=PurePosixPath(action["path"].replace("\\","/"))
            if not action["path"] or path.is_absolute() or ".." in path.parts or ":" in action["path"] or action["first"]<=0 or action["last"]<action["first"]:
                return {"kind":"invalid","reason":"invalid bounded read"}
        return action
    except (ValueError,TypeError,KeyError):
        return {"kind":"invalid","reason":"malformed action fields"}


def check_patch_scope(patch):
    paths=[]
    try:
        for line in patch.splitlines():
            if line.startswith(("rename ","copy ","GIT binary patch","Binary files")):
                raise ValueError("rename/copy/binary patches unsupported")
            if re.match(r"(?:new file mode|old mode|new mode|deleted file mode) ",line) and line.split()[-1] not in {"100644","100755"}:
                raise ValueError("nonregular file mode")
            if line.startswith("diff --git "):
                fields=shlex.split(line)
                if len(fields)!=4:raise ValueError("malformed diff paths")
                names=fields[2:]
            elif line.startswith(("--- ","+++ ")):
                names=[shlex.split(line[4:].split("\t",1)[0])[0]]
            else:continue
            for name in names:
                if name=="/dev/null":continue
                if not name.startswith(("a/","b/")):raise ValueError("nonrelative Git patch target")
                path=name[2:]
                parts=PurePosixPath(path).parts
                if ".." in parts or ":" in path or "\\" in path or not path.startswith("django/") or not path.endswith(".py") or path.startswith("django/test/"):
                    raise ValueError("candidate target outside permitted production Python sources")
                paths.append(path)
        if not paths:raise ValueError("patch has no permitted targets")
        return {"allowed":True,"paths":sorted(set(paths))}
    except (ValueError,IndexError) as exc:
        return {"allowed":False,"paths":sorted(set(paths)),"reason":str(exc)}


def capture_quality_assets(tasks):
    endpoint=load_tooling("agents_repair_runner")._endpoint()
    assets={"files_sha256":{},"clones":{}}
    for task_id in tasks:
        task=endpoint.Task.load(task_id)
        check=task.verify_clone()
        if not check["ok"]:raise ValueError("quality source clone drift")
        assets["clones"][task_id]=check
        for name in ("instance.json","host-tests.patch","reference.patch"):
            p=task.instance_dir/name
            assets["files_sha256"][p.relative_to(ROOT).as_posix()]=file_sha(p)
    py=endpoint.DEFAULT_PYTHON
    code="import sys,json,importlib.metadata; print(json.dumps({'python_version':sys.version,'distributions':sorted((d.metadata.get('Name',''),d.version) for d in importlib.metadata.distributions())}))"
    runtime=json.loads(subprocess.run([str(py),"-c",code],capture_output=True,text=True,check=True,timeout=90,
        env=endpoint.test_environment(ROOT)).stdout)
    runtime.update(python_executable=str(py),python_sha256=file_sha(py))
    assets["runtime"]=runtime
    return assets


def test_assets(workspace):
    root=workspace/"tests"
    return {p.relative_to(workspace).as_posix():file_sha(p) for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}


def score_candidate(task_id,patch,tag,out):
    scope=check_patch_scope(patch)
    if not scope["allowed"]:
        return {"accepted":False,"category":"candidate_scope_rejected","scope":scope}
    old=load_tooling("agents_repair_runner");endpoint=old._endpoint();task=endpoint.Task.load(task_id)
    endpoint._CLONE_BY_TAG[tag]=task.upstream_dir
    workspace=endpoint.workspace(tag)
    host=endpoint.apply_patch(workspace,task.instance_dir/"host-tests.patch")
    if not host["applied"]:raise ValueError("harness setup before candidate failed")
    before=test_assets(workspace)
    verdict=old.score_patch(old.Task.load(task_id),patch,tag=tag)
    after=test_assets(workspace)
    changed=sorted(p for p in before.keys()|after.keys() if before.get(p)!=after.get(p))
    proof={"before_sha256":sha(before),"after_sha256":sha(after),"changed":changed}
    (Path(out)/(tag+"-test-assets.json")).write_text(json.dumps(proof,indent=2),encoding="utf-8")
    verdict["scope"],verdict["evaluator_asset_integrity"]=scope,proof
    if changed:verdict.update(accepted=False,category="evaluation_assets_modified",harness_violation=True)
    return verdict


class FrozenInputFilter:
    def __init__(self,expected):self.expected=sha(expected);self.calls=0
    def __call__(self,data):
        if data.model_data.instructions is not None or sha(data.model_data.input)!=self.expected or self.calls:
            raise ValueError("SDK model boundary changed fixed H or attempted another turn")
        self.calls+=1
        return data.model_data


async def sdk_once(model,messages,sample_id,phase,expected_body=None):
    converted=Converter.items_to_messages(messages,model=MODEL,base_url="https://api.deepseek.com",should_replay_reasoning_content=None)
    with request_scope(sample_id,phase,expected_body_sha256=expected_body,expected_messages=converted,sdk_input_sha256=sha(messages)):
        result=await Runner.run(Agent(name="Matched checkpoint next action",model=model,instructions=None,
                    model_settings=sdk_settings()),input=deepcopy(messages),max_turns=1,
                    previous_response_id=None,conversation_id=None,auto_previous_response_id=False,
                    run_config=RunConfig(tracing_disabled=True,call_model_input_filter=FrozenInputFilter(messages)))
    return result.final_output


def mock_response(request):
    return httpx.Response(200,json={"id":"offline-probe","object":"chat.completion","created":0,"model":MODEL,
        "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":'{"read":{"path":"django/x.py","first":1,"last":2}}'}}],
        "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2,"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":1}})


def local_quality_gates(tasks,tag):
    old=load_tooling("agents_repair_runner");endpoint=old._endpoint()
    gates={}
    for task_id in tasks:
        task=endpoint.Task.load(task_id)
        clone_check=task.verify_clone()
        head=clone_check["head"]
        if not clone_check["ok"]:raise ValueError("upstream clone base/cleanliness gate failed")
        baseline_tag=f"{tag}-{task_id}-baseline"
        endpoint._CLONE_BY_TAG[baseline_tag]=task.upstream_dir
        workspace=endpoint.workspace(baseline_tag)
        host=endpoint.apply_patch(workspace,task.instance_dir/"host-tests.patch")
        if not host["applied"]:raise ValueError("hidden endpoint setup failed")
        baseline=endpoint.run_tests(workspace,task.fail_to_pass)
        reference=endpoint.evaluate_candidate(task,(task.instance_dir/"reference.patch").read_text(encoding="utf-8"),tag=f"{tag}-{task_id}-reference")
        empty=endpoint.evaluate_candidate(task,"",tag=f"{tag}-{task_id}-empty")
        gates[task_id]={"head":head,"baseline":baseline,"reference":reference,"empty":empty,
                       "passed":bool(baseline.get("ran")) and not baseline["passed"] and reference["accepted"] and not empty["accepted"]}
        if not gates[task_id]["passed"]:raise ValueError("quality endpoint controls failed")
    return gates


async def prepare_freeze(out,*,quality=True):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    selected=select_checkpoints(REPLAY);plan=build_plan(selected)
    if not selected:raise ValueError("no nonzero fixed-H intervention candidates")
    inputs,hashes={},{}
    for checkpoint in selected:
        for arm in ARMS:
            key=checkpoint["id"]+"-"+arm
            messages=checkpoint["snapshot"]["arms"][arm]["messages"]
            filename=key+".json"
            (out/filename).write_text(json.dumps(messages,ensure_ascii=False,indent=2),encoding="utf-8")
            inputs[key]=filename
            hashes[filename]=file_sha(out/filename)
    ledger=RequestLedger(out/"sdk-preflight",max_requests=len(inputs))
    model,client=make_sdk_model("offline-no-provider-key",AuditedTransport(ledger,httpx.MockTransport(mock_response)))
    converted={}
    try:
        for key,filename in inputs.items():
            messages=json.loads((out/filename).read_text(encoding="utf-8"))
            await sdk_once(model,messages,key,"initial")
            record=ledger.records[-1]
            request_filename="sdk-preflight/"+record["request_file"]
            hashes[request_filename]=file_sha(out/request_filename)
            converted[key]={"body_sha256":record["body_sha256"],"sdk_input_sha256":sha(messages),
                            "request_file":request_filename,"has_intervention":arm_hash_diff(selected,key)}
    finally:await client.close()
    tasks=list(dict.fromkeys(c["task"] for c in selected))
    gates=local_quality_gates(tasks,"agents-v2-20261014") if quality else None
    freeze={"schema":"agents_checkpoint_natural_v2","experiment_id":"agents-checkpoint-natural-v2-20261014",
        "tasks":tasks,"checkpoints":[{k:c[k] for k in ("id","task","recorded_digest")} for c in selected],
        "plan":plan,"samples":len(plan),"max_requests":len(plan)*2,"cap_usd":1.,"max_body_bytes":24000,
        "model":MODEL,"output_max":2048,"thinking":"disabled","temperature":0,"repeats":3,
        "prices":PRICES,"pricing_source":"https://api-docs.deepseek.com/quick_start/pricing/",
        "inputs":inputs,"converted":converted,"files_sha256":hashes,
        "sources_sha256":{p:file_sha(ROOT/p) for p in SOURCE_FILES},
        "environment":{p:importlib.metadata.version(p) for p in ("openai-agents","openai","httpx")},
        "quality_gates":gates,"quality_assets":capture_quality_assets(tasks),
        "paid_run_allowed":gates is not None and all(g["passed"] for g in gates.values()),
        "selection_rule":"first two distinct nonzero guarded matched H per task in recorded order; no replacement",
        "repair_prompt":REPAIR,"scope":"local next action only; read not executed; patch independently scored"}
    (out/"freeze.json").write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"freeze.sha256").write_text(file_sha(out/"freeze.json"),encoding="ascii")
    return freeze


def arm_hash_diff(selected,key):
    for c in selected:
        for arm in ARMS:
            if key==c["id"]+"-"+arm:
                return c["snapshot"]["arms"][arm]["digest"]!=c["snapshot"]["arms"]["none"]["digest"]
    raise ValueError("unknown arm key")


def verify_freeze(out):
    out=Path(out);freeze=json.loads((out/"freeze.json").read_text(encoding="utf-8"))
    if freeze.get("schema")!="agents_checkpoint_natural_v2":raise ValueError("freeze schema mismatch")
    if (out/"freeze.sha256").read_text().strip()!=file_sha(out/"freeze.json"):raise ValueError("freeze SHA mismatch")
    for name,expected in freeze["files_sha256"].items():
        path=(out/name).resolve()
        if not path.is_relative_to(out.resolve()) or file_sha(path)!=expected:raise ValueError("frozen input SHA mismatch")
    for name,expected in freeze["sources_sha256"].items():
        if file_sha(ROOT/name)!=expected:raise ValueError("frozen source drift")
    if any(importlib.metadata.version(p)!=v for p,v in freeze["environment"].items()):raise ValueError("SDK dependency drift")
    if capture_quality_assets(freeze["tasks"])!=freeze["quality_assets"]:raise ValueError("frozen quality assets/runtime drift")
    if len(freeze["plan"])!=freeze["samples"] or freeze["max_requests"]!=freeze["samples"]*2:raise ValueError("matrix count mismatch")
    return freeze


def write_samples(out,samples):
    (out/"samples.json").write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding="utf-8")


async def finalize_batch(client,out,freeze,freeze_hash,samples,ledger,stopped,mock):
    cleanup_error=None
    try:await client.close()
    except BaseException as exc:cleanup_error=type(exc).__name__
    summary={"experiment_id":freeze["experiment_id"],"mock":mock,"samples_planned":freeze["samples"],
        "samples_recorded":len(samples),"samples_completed":sum(s["status"]=="completed" for s in samples),
        "http_requests":len(ledger.records),"provider_requests":0 if mock else len(ledger.records),
        "reserved_peak_allmiss_usd":ledger.reserved_usd,"stopped":stopped,"cleanup_error":cleanup_error,
        "freeze_sha256":freeze_hash,"ledger":ledger.records,
        "interpretation":"same-H local next-action development; not full-task cost or lifecycle benefit"}
    (Path(out)/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


async def run_batch(freeze_dir,out,key,*,mock=False,score=True):
    freeze=verify_freeze(freeze_dir)
    if not mock and not freeze["paid_run_allowed"]:raise ValueError("quality gate not passed")
    freeze_dir=Path(freeze_dir);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    claim=freeze_dir/("mock.claim" if mock else "paid.claim")
    with claim.open("x",encoding="utf-8") as f:f.write(str(out.resolve()))
    ledger=RequestLedger(out/"http",max_requests=freeze["max_requests"],cap_usd=freeze["cap_usd"],max_body_bytes=freeze["max_body_bytes"])
    inner=httpx.MockTransport(mock_response) if mock else None
    model,client=make_sdk_model(key,AuditedTransport(ledger,inner))
    samples,stopped=[],None
    try:
        for row in freeze["plan"]:
            if shutil.disk_usage(ROOT).free<2*1024**3:
                stopped={"reason":"disk_floor_before_sample","sample_id":row["sample_id"]};break
            sample={**row,"status":"started","attempts":[],"action":None,"acceptance":None}
            samples.append(sample);write_samples(out,samples)
            arm_key=row["checkpoint_id"]+"-"+row["arm"]
            messages=json.loads((freeze_dir/freeze["inputs"][arm_key]).read_text(encoding="utf-8"))
            sample["has_intervention"]=freeze["converted"][arm_key]["has_intervention"]
            try:
                verify_freeze(freeze_dir)
                for phase in ("initial","repair"):
                    reply=await sdk_once(model,messages,row["sample_id"],phase,
                        freeze["converted"][arm_key]["body_sha256"] if phase=="initial" else None)
                    action=parse_action(reply)
                    sample["attempts"].append({"phase":phase,"reply":reply,"action":action,
                        "request_index":ledger.records[-1]["index"]})
                    if ledger.records[-1]["status"]!="returned":raise ValueError("provider usage/accounting violation")
                    if any(ledger.records[-1]["usage"][k] is None for k in ("prompt_tokens","completion_tokens")):
                        raise ValueError("provider usage missing; stop without pretending zero")
                    if action["kind"]!="invalid" or phase=="repair":break
                    ledger.authorize_repair(row["sample_id"])
                    messages+= [{"role":"assistant","content":reply},{"role":"user","content":freeze["repair_prompt"]}]
                sample["action"]=action
                if action["kind"]=="patch" and score:
                    verify_freeze(freeze_dir)
                    sample["acceptance"]=await asyncio.to_thread(score_candidate,row["task"],action["patch"],
                        "natural-v2-"+row["sample_id"],out)
                    if sample["acceptance"].get("harness_violation") or (sample["acceptance"].get("reason") or "").startswith("harness_error"):
                        raise ValueError("local quality endpoint setup error")
                sample["status"]="completed"
            except Exception as exc:
                sample["status"]="error";sample["error_type"]=type(exc).__name__
                stopped={"reason":type(exc).__name__,"sample_id":row["sample_id"]}
                write_samples(out,samples);break
            write_samples(out,samples)
            print(f"[{len(samples)}/{freeze['samples']}] {row['sample_id']} action={sample['action']['kind']} requests={len(ledger.records)}",flush=True)
    finally:
        summary=await finalize_batch(client,out,freeze,file_sha(freeze_dir/"freeze.json"),samples,ledger,stopped,mock)
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("prepare","mock","run"));parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--freeze",type=Path);parser.add_argument("--skip-quality",action="store_true")
    args=parser.parse_args()
    if args.mode=="prepare":
        result=asyncio.run(prepare_freeze(args.out,quality=not args.skip_quality))
        print(json.dumps({k:result[k] for k in ("samples","max_requests","cap_usd","tasks","paid_run_allowed")},indent=2));return
    if args.freeze is None:raise ValueError("--freeze required")
    key="offline" if args.mode=="mock" else os.environ.pop("DEEPSEEK_API_KEY","")
    os.environ.pop("OPENAI_API_KEY",None)
    if not key:raise ValueError("provider key absent; inject without printing")
    result=asyncio.run(run_batch(args.freeze,args.out,key,mock=args.mode=="mock",score=args.mode!="mock"))
    print(json.dumps({k:result[k] for k in ("samples_planned","samples_completed","provider_requests","reserved_peak_allmiss_usd","stopped")},indent=2))


if __name__=="__main__":main()
