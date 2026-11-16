"""Distribution-only synthetic entrypoint tests, no inference or real data."""
from pathlib import Path
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
@pytest.fixture
def package(tmp_path):
    base=tmp_path/"package";m=json.loads((ROOT/"release-manifest.json").read_bytes())
    for name in ["release-manifest.json",*m["files"]]:
        p=base/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,p)
    # This fixture uses the test interpreter, not an installer or downloaded runtime.
    src=ROOT/".install/receipt.json"
    r=json.loads(src.read_bytes());r["release_sha256"]=digest(base/"release-manifest.json")
    r["python"]=sys.executable
    (base/".venv").mkdir();(base/".venv/pyvenv.cfg").write_text("artificial test identity")
    r["environment_identity"]=digest(base/".venv/pyvenv.cfg")
    (base/".install").mkdir();(base/".install/receipt.json").write_text(json.dumps(r))
    return base
def entry(base):
    spec=importlib.util.spec_from_file_location("package_entry",base/"scripts/run_synthetic_demo.py")
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_every_shipped_module_imports():
    for path in (ROOT/"fourw").glob("*.py"):
        importlib.import_module("fourw."+path.stem)
def test_prepare_roundtrip_and_collision(package,capfd):
    m=entry(package);m.prepare();base=package/"runs/demo"
    p=m.load(base/"prepared.json")
    assert len(p["requests"])==2
    reqs=[m.load(base/r) for r in p["requests"]]
    assert list(map(lambda r:len(r["cases"]),reqs))==[16,1]
    assert len({c["case_ref"] for r in reqs for c in r["cases"]})==17
    expected={k:digest(base/k) for k in p["artifacts"]}
    m.prepare();assert expected=={k:digest(base/k) for k in expected}
    (base/"source.xlsx").write_bytes(b"corrupt synthetic container")
    with pytest.raises(ValueError,match="prepared_input_changed"):m.prepare()
    capfd.readouterr()
def test_accept_preserves_first_response_and_rejects_changed_input(package,capfd):
    from argparse import Namespace
    m=entry(package);m.prepare();base=package/"runs/demo";req=m.load(base/"request-001.json")
    raw=json.dumps({"request_id":req["request_id"],"records":[]}).encode()
    response=package/"first-response.json";response.write_bytes(raw)
    args=Namespace(request="request-001.json",response=str(response))
    m.accept(args);m.accept(args)
    assert (base/"request-001.json.first-response").read_bytes()==raw
    response.write_text("{}")
    with pytest.raises(ValueError,match="different_first_response"):m.accept(args)
    capfd.readouterr()
def test_response_only_delivery_recovers_verified_export(package,capfd):
    from argparse import Namespace
    m=entry(package);m.prepare();base=package/"runs/demo";p=m.load(base/"prepared.json")
    for ref in p["requests"]:
        req=m.load(base/ref);response=package/(ref+".json")
        response.write_text(json.dumps({"request_id":req["request_id"],"records":[]}))
        m.accept(Namespace(request=ref,response=str(response)))
    m.finish();assert m.load(base/"completion.json")["status"]=="complete_with_failures"
    original=digest(base/"scored/results.xlsx")
    (base/"completion.json").unlink()
    m.finish();assert digest(base/"scored/results.xlsx")==original
    m.finish();assert digest(base/"scored/results.xlsx")==original
    (base/(p["requests"][0]+".first-response")).write_text("{}")
    with pytest.raises(ValueError,match="completed_response_changed"):m.finish()
    capfd.readouterr()
def test_modified_installation_is_preserved(package,capfd):
    m=entry(package);r=m.load(package/".install/receipt.json")
    r["environment_files"]={"pyvenv.cfg":r["environment_identity"]}
    (package/".install/receipt.json").write_text(json.dumps(r))
    (package/".venv/user-change.txt").write_text("keep")
    with pytest.raises(ValueError,match="modified_installation_preserved"):m.uninstall()
    assert (package/".venv/user-change.txt").exists()
    capfd.readouterr()
def test_lock_rejects_concurrent_writer(package):
    m=entry(package)
    with m.mutation_lock():
        child=subprocess.run([sys.executable,"-B",str(package/"scripts/run_synthetic_demo.py"),"prepare"],capture_output=True,timeout=20)
    assert child.returncode==1 and b"operation_owned_by_another_process" in child.stdout
    assert not (package/"runs/demo").exists()
def test_install_parameter_omits_empty_value():
    script=(ROOT/"scripts/install.ps1").read_text()
    assert '$ArtifactRuntime -ne ""' in script


def test_duplicate_install_uses_installed_interpreter(package,monkeypatch,capfd):
    from argparse import Namespace
    m=entry(package);seen=[]
    monkeypatch.setattr(m,"execute",lambda args,timeout=120:seen.append(args) or b"")
    m.install(Namespace(node="node",artifact_runtime="",development=False))
    assert len(seen)==1 and Path(seen[0][0])==package/".venv/Scripts/python.exe"
    assert seen[0][-1]=="verify"
    capfd.readouterr()
