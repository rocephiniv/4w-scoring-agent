"""Synthetic evaluation entrypoint. Output is metadata only."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv
from contextlib import contextmanager
import msvcrt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def digest(path):return sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open("x",encoding="utf-8",newline="\n") as f:json.dump(value,f,sort_keys=True,indent=2);f.write("\n")
def load(path):return json.loads(Path(path).read_bytes())
def manifest():
 m=load(ROOT/"release-manifest.json")
 for ref,item in m["files"].items():
  p=ROOT/ref
  if not p.is_file() or digest(p)!=item["sha256"]:raise ValueError("package_identity_changed")
 return m
def execute(argv,timeout=120):
 p=subprocess.run(argv,capture_output=True,timeout=timeout)
 if p.returncode:raise ValueError("dependency_or_runtime_command_failed")
 return p.stdout
def tree_identity(directory):
 return {p.relative_to(directory).as_posix():digest(p) for p in sorted(directory.rglob("*"))
         if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
@contextmanager
def mutation_lock():
 path=ROOT/".install/mutation.lock";path.parent.mkdir(parents=True,exist_ok=True)
 with path.open("a+b") as stream:
  if path.stat().st_size==0:stream.write(b"0");stream.flush()
  stream.seek(0)
  try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise ValueError("operation_owned_by_another_process") from None
  try:yield
  finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
def environment():
 r=load(ROOT/".install/receipt.json")
 if r["release_sha256"]!=digest(ROOT/"release-manifest.json"):raise ValueError("installed_release_changed")
 if digest(r["node"])!=r["node_sha256"]:raise ValueError("node_identity_changed")
 package=Path(r["artifact_runtime"])/"@oai/artifact-tool/package.json"
 if digest(package)!=r["artifact_package_sha256"]:raise ValueError("artifact_runtime_changed")
 if digest(ROOT/".venv/pyvenv.cfg")!=r["environment_identity"]:raise ValueError("environment_changed")
 return r
def exported(node,runtime,plan,out):
 execute([node,str(ROOT/"workbook.mjs"),runtime,str(plan),str(out)],60)
def installed_inventory(python):
 return json.loads(execute([str(python),"-B","-m","pip","list","--format=json"],60))
def install(args):
 manifest()
 if os.name!="nt" or sys.version_info[:2]!=(3,13):raise ValueError("requires_Windows_Python_3_13")
 env=ROOT/".venv";receipt=ROOT/".install/receipt.json"
 if receipt.exists():
  execute([str(env/"Scripts/python.exe"),"-B",str(Path(__file__).resolve()),"verify"],60)
  print(json.dumps({"status":"already_installed_verified"}));return
 if env.exists():raise ValueError("partial_environment_preserved")
 node=shutil.which(args.node)
 if not node:raise ValueError("node_unavailable")
 node=str(Path(node).resolve())
 version=execute([node,"--version"]).decode().strip()
 if int(version.lstrip("v").split(".")[0])<22:raise ValueError("node_22_required")
 runtime=Path(args.artifact_runtime) if args.artifact_runtime else Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
 package=runtime/"@oai/artifact-tool/package.json"
 if not package.is_file():raise ValueError("independent_artifact_runtime_required")
 venv.EnvBuilder(with_pip=True).create(env)
 python=env/"Scripts/python.exe"
 req=ROOT/("requirements-dev.txt" if args.development else "requirements.txt")
 execute([str(python),"-B","-m","pip","--isolated","--disable-pip-version-check","install","--timeout","30","--retries","2","--index-url","https://pypi.org/simple","-r",str(req)],240)
 execute([str(python),"-B","-m","pip","check"],30)
 # No clinical inference: verify parser and the strict existing packet serializer.
 with tempfile.TemporaryDirectory(prefix="fourw-install-",dir=ROOT) as t:
  out=Path(t)/"export"
  exported(node,str(runtime.resolve()),ROOT/"tests/fixtures/review/public-plan.json",out)
  check="import json,sys;from pathlib import Path;from hashlib import sha256;from fourw.runs import canonical_json;from fourw.packet_guard import verify_issued_packet;p=Path(sys.argv[1]);q=Path(sys.argv[2]);u=json.loads((q/'tests/fixtures/review/public-plan.json').read_bytes());v=json.loads((q/'tests/fixtures/review/private-linkage.json').read_bytes());verify_issued_packet(p,u,v,sha256(canonical_json(v)).hexdigest())"
  e=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE="1")
  p=subprocess.run([str(python),"-B","-c",check,str(out/"results.xlsx"),str(ROOT)],capture_output=True,env=e,timeout=30)
  if p.returncode:raise ValueError("artifact_serialization_profile_incompatible")
 r={"release_sha256":digest(ROOT/"release-manifest.json"),"python":str(python),"base_python":sys.executable,"node":node,"node_version":version,"node_sha256":digest(node),"artifact_package_sha256":digest(package),"artifact_runtime":str(runtime.resolve()),"artifact_version":load(package)["version"],"environment_identity":digest(env/"pyvenv.cfg"),"inventory":installed_inventory(python),"development":args.development,"native_invocation":"unverified","clinical_validity":"unverified"}
 r["environment_files"]=tree_identity(env)
 save(receipt,r)
 execute([str(python),"-B",str(Path(__file__).resolve()),"verify"],60)
 print(json.dumps({"status":"installed_transport_verified","native_invocation":"unverified","receipt":str(receipt)}))
def verify():
 manifest();r=environment()
 if digest(ROOT/".venv/pyvenv.cfg")!=r["environment_identity"]:raise ValueError("environment_changed")
 from fourw.specialist import load_authority,REQUIRED
 load_authority(ROOT,{ref:digest(ROOT/ref) for ref in REQUIRED})
 from fourw.packet_guard import load_profile
 load_profile()
 import tomllib
 agent=tomllib.loads((ROOT/".codex/agents/4w-scoring-agent.toml").read_text())
 if agent["name"]!="4W Scoring Agent" or "historical_calibration_only" in agent["developer_instructions"]:raise ValueError("agent_scope_changed")
 if installed_inventory(r["python"])!=r["inventory"]:raise ValueError("installed_dependencies_changed")
 print(json.dumps({"status":"verified","authority":"4W-Harmonized-v1-docs.1","native_invocation":r["native_invocation"]}))
def uninstall():
 manifest();r=environment()
 env=ROOT/".venv"
 if env.is_symlink() or env.resolve().parent!=ROOT.resolve() or tree_identity(env)!=r["environment_files"]:
  raise ValueError("modified_installation_preserved")
 # PowerShell performs deletion only after this verification process exits.
 print(json.dumps({"status":"uninstall_verified","path":str(env)}))
def prepare():
 manifest();r=environment()
 from openpyxl import Workbook
 from fourw.intake import source_identity,Mapping,inspect_workbook
 from fourw.assessments import CaseContext,AssessmentValidator
 from fourw.specialist import REQUIRED,SCHEMA,make_request,load_authority
 from fourw.runs import canonical_json
 base=ROOT/"runs/demo"
 if (base/"prepared.json").exists():
  p=load(base/"prepared.json")
  if any(digest(base/k)!=v for k,v in p["artifacts"].items()):raise ValueError("prepared_input_changed")
  print(json.dumps({"status":"prepared_reused","requests":len(p["requests"]),"path":str(base)}));return
 if base.exists():raise ValueError("partial_preparation_preserved")
 base.mkdir(parents=True)
 w=Workbook();ws=w.active;ws.title="Synthetic";ws.append(["Indication"])
 for _ in range(17):ws.append(["Synthetic shared text."][0:1])
 # Artificial text only, generated internally. No path argument accepts cases.
 w.save(base/"source.xlsx")
 ident=source_identity(base/"source.xlsx")
 mapping=Mapping(version="synthetic-demo-v1",approval_ref="private-evaluation",source=ident,sheets=("Synthetic",),sheet="Synthetic",header_row=1,headers=("Indication",),first_row=2,last_row=18,row_unit="artificial demonstration row",indication="Indication",missing_values=(None,""))
 intake=inspect_workbook(base/"source.xlsx",mapping)
 if intake.status!="accepted":raise ValueError("synthetic_intake_failed")
 contexts=[CaseContext(rec.case_ref,"synthetic-demo",rec.cells["Indication"].value,ident.sha256,f"Synthetic!A{rec.source_row}",{"decision":"eligible","reason":"Artificial nonclinical test row","review_status":"first-pass"}) for rec in intake.records]
 save(base/"contexts.json",[asdict(c) for c in contexts])
 hashes={ref:digest(ROOT/ref) for ref in REQUIRED}
 # Canonical-only immutable knowledge view. The ordinary loader owns rule checks.
 class CanonicalOnly:
  def load(self,release,expected,documents,**kw):
   if expected!=sha256(canonical_json(documents)).hexdigest():raise ValueError("knowledge_identity")
   return {"release_id":release,"sha256":expected,"authority":"4W-Harmonized-v1-docs.1","documents":documents,"lessons":[]}
 from fourw.knowledge import DOCUMENTS
 docs={k:hashes[k] for k in DOCUMENTS};know=sha256(canonical_json(docs)).hexdigest()
 reqs=[]
 for i in range(0,len(contexts),16):
  request=make_request(ROOT,hashes,contexts[i:i+16],"initial_score",CanonicalOnly(),"canonical-only",know,{"scope":"synthetic_build_only","authorization_ref":"private-evaluation-synthetic-demo","rescore_authorization_ref":None},AssessmentValidator(ROOT/SCHEMA))
  ref=f"request-{i//16+1:03}.json";save(base/ref,request);reqs.append(ref)
 refs=["source.xlsx","contexts.json"]+reqs
 save(base/"prepared.json",{"artifacts":{k:digest(base/k) for k in refs},"requests":reqs,"release_sha256":digest(ROOT/"release-manifest.json"),"knowledge_sha256":know})
 print(json.dumps({"status":"prepared","requests":len(reqs),"records":len(contexts),"path":str(base)}))
def accept(args):
 manifest();base=ROOT/"runs/demo";p=load(base/"prepared.json")
 if any(digest(base/k)!=v for k,v in p["artifacts"].items()):raise ValueError("prepared_input_changed")
 if p["release_sha256"]!=digest(ROOT/"release-manifest.json"):raise ValueError("run_release_changed")
 if args.request not in p["requests"]:raise ValueError("unknown_request")
 from fourw.assessments import AssessmentValidator,CaseContext
 from fourw.specialist import accept_response,SCHEMA
 from fourw.runs import canonical_json
 request=load(base/args.request);all_contexts=[CaseContext(**x) for x in load(base/"contexts.json")]
 contexts=[c for c in all_contexts if c.case_ref in {x["case_ref"] for x in request["cases"]}]
 raw=Path(args.response).read_bytes();dest=base/(args.request+".first-response")
 if dest.exists():
  if dest.read_bytes()!=raw:raise ValueError("different_first_response")
 else:
  with dest.open("xb") as f:f.write(raw)
 result=accept_response(request,contexts,raw,AssessmentValidator(ROOT/SCHEMA))
 # Preserve exact model response. Checked records are reconstructed in finish.
 receipt=base/(args.request+".accepted.json")
 if not receipt.exists():save(receipt,{"response_sha256":digest(dest),"status":result["status"],"request_sha256":digest(base/args.request)})
 print(json.dumps({"status":result["status"],"records":len(result["records"])}))
def finish():
 manifest();r=environment();base=ROOT/"runs/demo";p=load(base/"prepared.json")
 if any(digest(base/k)!=v for k,v in p["artifacts"].items()):raise ValueError("prepared_input_changed")
 if p["release_sha256"]!=digest(ROOT/"release-manifest.json"):raise ValueError("run_release_changed")
 receipt=base/"completion.json"
 if receipt.exists():
  saved=load(receipt)
  if any(digest(base/k)!=v for k,v in saved["response_artifacts"].items()):raise ValueError("completed_response_changed")
  if digest(base/"scored/results.xlsx")!=saved["workbook_sha256"]:raise ValueError("completed_output_changed")
  print(json.dumps({"status":"complete_reused","workbook":str(base/"scored/results.xlsx")}));return
 from fourw.assessments import AssessmentValidator,CaseContext
 from fourw.specialist import accept_response,SCHEMA
 from fourw.delivery import make_plan,verify_workbook
 contexts=[CaseContext(**x) for x in load(base/"contexts.json")];validator=AssessmentValidator(ROOT/SCHEMA);records=[]
 for ref in p["requests"]:
  a=load(base/(ref+".accepted.json"));response=base/(ref+".first-response")
  if digest(response)!=a["response_sha256"] or digest(base/ref)!=a["request_sha256"]:raise ValueError("response_or_request_changed")
  request=load(base/ref);cs=[c for c in contexts if c.case_ref in {x["case_ref"] for x in request["cases"]}]
  checked=accept_response(request,cs,response.read_bytes(),validator)
  records.extend({**row,"context":CaseContext(**row["context"])} for row in checked["records"])
 if len(records)!=len(contexts) or len({x["context"].case_ref for x in records})!=len(contexts):raise ValueError("coverage_failed")
 plan=make_plan("synthetic-demo",records,validator,{"scope":"synthetic_build_only"})
 path=base/"plan.json"
 if not path.exists():save(path,plan)
 elif load(path)!=plan:raise ValueError("saved_plan_changed")
 out=base/"scored"
 if out.exists():
  generation=load(out/"generation.json")
  if generation["plan_sha256"]!=digest(path) or generation["workbook_sha256"]!=digest(out/"results.xlsx"):
   raise ValueError("partial_export_requires_reconciliation")
 else:
  exported(r["node"],r["artifact_runtime"],path,out)
 result=verify_workbook(out/"results.xlsx",plan)
 if digest(base/"source.xlsx")!=p["artifacts"]["source.xlsx"]:raise ValueError("source_changed")
 save(receipt,{"status":plan["processing_state"],"records":len(records),"workbook_sha256":digest(out/"results.xlsx"),"source_sha256":p["artifacts"]["source.xlsx"],"clinical_validity":"unverified","response_artifacts":{ref+suffix:digest(base/(ref+suffix)) for ref in p["requests"] for suffix in (".first-response",".accepted.json")},"verification":result})
 print(json.dumps({"status":plan["processing_state"],"records":len(records),"workbook":str(out/"results.xlsx")}))
def main():
 parser=argparse.ArgumentParser()
 sub=parser.add_subparsers(dest="action",required=True)
 x=sub.add_parser("install");x.add_argument("--node",default="node");x.add_argument("--artifact-runtime",default="");x.add_argument("--development",action="store_true")
 for name in ("verify","uninstall","prepare","finish"):sub.add_parser(name)
 a=sub.add_parser("accept");a.add_argument("--request",required=True);a.add_argument("--response",required=True)
 args=parser.parse_args()
 try:
  with mutation_lock() if args.action!="verify" else __import__("contextlib").nullcontext():
   if args.action=="install":install(args)
   elif args.action=="accept":accept(args)
   else:globals()[args.action]()
 except Exception as error:
  print(json.dumps({"status":"failed","error_type":type(error).__name__,"code":str(error) if isinstance(error,ValueError) and re_safe(str(error)) else "inspect_local_failure_without_repeating_inference"}));return 1
 return 0
def re_safe(value):return bool(value) and all(c.isalnum() or c=="_" for c in value)
if __name__=="__main__":raise SystemExit(main())
